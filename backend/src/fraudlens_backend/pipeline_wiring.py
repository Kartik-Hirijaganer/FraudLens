"""Summary: Dependency-injection facade for the investigation pipeline.

Key classes:
- PipelineComponents: process-wide scorer, explainer, retriever, and drafter dependencies.

Key functions:
- build_pipeline_components: construct process-wide dependencies from settings.
- build_pipeline_deps: assemble tenant-scoped dependencies for one run.

Notes:
- Public and test-facing imports remain available through explicit re-exports.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.agents.contracts import AgentExecutionRecord
from fraudlens_backend.agents.resume import AgentExecutionReplay
from fraudlens_backend.db.repositories import (
    AgentExecutionRepository,
    AnalysisRunRepository,
    DashboardRepository,
    ModelRegistryRepository,
    RuleRepository,
    SarDraftRepository,
    TransactionRepository,
    load_llm_daily_budget_usd,
)
from fraudlens_backend.pipeline_policy import (
    build_pipeline_input,
    load_risk_policy,
    resolve_scoring_pointer,
    resolve_workflow_mode,
)
from fraudlens_backend.pipeline_ports import (
    ExplainerAdapter,
    RetrieverAdapter,
    RulesAdapter,
    ScorerAdapter,
    _anchored,
)
from fraudlens_backend.pipeline_runs import PipelineRunStore, RunManager, _RunState
from fraudlens_backend.rag import build_embedder
from fraudlens_backend.sar import build_sar_drafter
from fraudlens_backend.sar.drafter_fallback import LiveAgentFallbackDrafter
from fraudlens_backend.sar.drafter_replay import PersistedSarDrafter, resume_drafter
from fraudlens_backend.sar.factory import AgentDrafterFactory, build_agent_drafter_factory
from fraudlens_backend.settings import AppSettings, _config_anchored
from fraudlens_backend.telemetry import log_llm_call
from fraudlens_core import RuleRegistry
from fraudlens_ml.pipeline import EventEmitter, PipelineDeps
from fraudlens_ml.rag import Retriever
from fraudlens_ml.sar import SarDrafter
from fraudlens_ml.scoring import Explainer, ModelCache, Scorer

__all__ = [
    "ExplainerAdapter",
    "PipelineComponents",
    "PipelineRunStore",
    "RetrieverAdapter",
    "RulesAdapter",
    "RunManager",
    "ScorerAdapter",
    "_RunState",
    "_anchored",
    "_config_anchored",
    "build_pipeline_components",
    "build_pipeline_deps",
    "build_pipeline_input",
    "load_risk_policy",
    "resolve_scoring_pointer",
    "resolve_workflow_mode",
]


@dataclass(frozen=True)
class PipelineComponents:
    """The process-wide heavy singletons reused across runs (warm model cache + retriever)."""

    cache: ModelCache
    scorer: Scorer
    explainer: Explainer
    retriever: Retriever
    drafter: SarDrafter
    agent_drafter_factory: AgentDrafterFactory | None
    agent_config: Any
    agent_prompts: dict[Any, Any]
    agent_max_cost_usd: Decimal
    mock_revision_external_id_suffix: str | None


def build_pipeline_components(settings: AppSettings) -> PipelineComponents:
    """Construct the process-wide pipeline singletons from settings (paths anchored at the CWD)."""
    from fraudlens_backend.agents.config import AgentRole, load_agents_config  # noqa: PLC0415
    from fraudlens_backend.agents.prompts import AgentPromptTemplate  # noqa: PLC0415
    from fraudlens_backend.agents.runtime import estimate_workflow_max_cost_usd  # noqa: PLC0415
    from fraudlens_backend.agents.tools import AGENT_TOOL_NAMES  # noqa: PLC0415
    from fraudlens_backend.portfolio_demo import load_portfolio_demo_config  # noqa: PLC0415
    from fraudlens_llm import get_llm_settings, load_catalog  # noqa: PLC0415

    cache = ModelCache(_anchored(settings.model_artifacts_dir))
    embedder = build_embedder(settings)
    retriever = Retriever(
        persist_dir=_anchored(settings.rag_index_dir),
        collection=settings.rag_collection,
        embedder=embedder,
        rag_version=embedder.provenance.rag_version,
        min_similarity=settings.investigation_rag_min_similarity,
    )
    catalog = load_catalog(get_llm_settings().catalog_path)
    agent_path = _config_anchored(settings.multi_agent_config_file)
    agent_config = load_agents_config(
        catalog=catalog,
        available_tools=AGENT_TOOL_NAMES,
        path=agent_path,
    )
    agent_prompts = {
        role: AgentPromptTemplate.load(role, agent_config.agents.for_role(role).prompt_id)
        for role in AgentRole
    }
    revision_suffix: str | None = None
    if settings.llm_mode == "mock":
        portfolio = load_portfolio_demo_config(settings=settings)
        scenario = next(
            item
            for item in portfolio.scenarios
            if item.scenario_id == portfolio.execution.mock_agent_revision_scenario
        )
        revision_suffix = scenario.external_id_suffix
    return PipelineComponents(
        cache=cache,
        scorer=Scorer(cache),
        explainer=Explainer(),
        retriever=retriever,
        drafter=build_sar_drafter(settings),
        agent_drafter_factory=(
            build_agent_drafter_factory(catalog=catalog, config=agent_config)
            if settings.llm_mode == "live"
            else None
        ),
        agent_config=agent_config,
        agent_prompts=agent_prompts,
        agent_max_cost_usd=estimate_workflow_max_cost_usd(agent_config, catalog),
        mock_revision_external_id_suffix=revision_suffix,
    )


async def build_pipeline_deps(  # noqa: PLR0913 - per-run DI assembly from injected collaborators (keyword-only).
    *,
    components: PipelineComponents,
    session: AsyncSession,
    settings: AppSettings,
    agency_id: uuid.UUID,
    run_id: uuid.UUID,
    transaction_id: uuid.UUID,
    emit: EventEmitter,
    model_override: str | None = None,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
    workflow_mode: str = "single_writer",
    lease_owner: str | None = None,
    fencing_token: int | None = None,
) -> PipelineDeps:
    """Resolve the routed pointer/rule-set/policy and assemble the per-run PipelineDeps."""
    registry = ModelRegistryRepository(session)
    pointer, was_canary = await resolve_scoring_pointer(
        registry,
        routing_key=str(transaction_id),
        model_override=model_override,
        allow_candidate_fallback=settings.is_candidate_scoring_fallback_enabled,
    )
    definitions = await RuleRepository(session, agency_id).load_definitions()
    risk_policy = await load_risk_policy(session)
    store = PipelineRunStore(
        session=session,
        run_id=run_id,
        transaction_id=transaction_id,
        analysis=AnalysisRunRepository(session, agency_id),
        registry=registry,
        sar=SarDraftRepository(session, agency_id),
        review_low_confidence_margin=settings.review_low_confidence_margin,
        lease_owner=lease_owner,
        fencing_token=fencing_token,
    )
    persisted_draft = await SarDraftRepository(session, agency_id).get_for_run(run_id)
    drafter = resume_drafter(components.drafter, persisted_draft)
    if workflow_mode == "multi_agent" and not isinstance(drafter, PersistedSarDrafter):
        if sessionmaker is None:
            raise RuntimeError("Multi-agent workflow requires a session factory")

        from fraudlens_backend.agents.mock import MockAgentTeam  # noqa: PLC0415
        from fraudlens_backend.agents.tools import EvidenceToolset  # noqa: PLC0415

        toolset = EvidenceToolset(
            sessionmaker,
            agency_id,
            run_id,
            retriever=RetrieverAdapter(components.retriever),
            history_window_hours=settings.investigation_history_window_hours,
            history_limit=settings.investigation_history_max,
        )

        async def record_execution(record: AgentExecutionRecord) -> None:
            """Persist and log one agent attempt before publishing its completed event."""
            async with sessionmaker() as execution_session:
                await AgentExecutionRepository(execution_session, agency_id).save_from_record(
                    run_id=run_id,
                    record=record,
                )
                await execution_session.commit()
            requested_model = components.agent_config.agents.for_role(record.agent).model
            log_llm_call(
                model=record.model_id,
                prompt_version=record.prompt_version,
                prompt_hash=record.prompt_hash,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                total_tokens=record.total_tokens,
                cost_usd=record.cost_usd,
                fallback_count=int(
                    settings.llm_mode == "live" and record.model_id != requested_model
                ),
                latency_ms=record.latency_ms,
                run_id=str(run_id),
                agency_id=str(agency_id),
                agent=record.agent.value,
                attempt=record.attempt,
            )

        replay = AgentExecutionReplay(
            sessionmaker,
            agency_id=agency_id,
            run_id=run_id,
        )

        if settings.llm_mode == "mock":
            transaction = await TransactionRepository(session, agency_id).get(transaction_id)
            request_revision = bool(
                transaction is not None
                and components.mock_revision_external_id_suffix
                and transaction.external_id.endswith(components.mock_revision_external_id_suffix)
            )
            drafter = MockAgentTeam(
                run_id=run_id,
                config=components.agent_config,
                prompts=components.agent_prompts,
                single_writer=components.drafter,
                record_execution=record_execution,
                replay=replay,
                request_revision=request_revision,
            )
        else:
            if components.agent_drafter_factory is None:
                raise RuntimeError("Live agent drafter factory is unavailable")
            daily_limit = await load_llm_daily_budget_usd(session, agency_id=agency_id)
            daily_spent = await DashboardRepository(session, agency_id).sar_cost_today(
                as_of=datetime.now(UTC)
            )
            primary = components.agent_drafter_factory(
                toolset,
                run_id=run_id,
                record_execution=record_execution,
                replay=replay,
                daily_limit_usd=daily_limit,
                daily_spent_usd=daily_spent,
            )
            drafter = (
                LiveAgentFallbackDrafter(primary=primary, fallback=components.drafter)
                if components.agent_config.workflow.fallback_to_single_writer
                else primary
            )
    return PipelineDeps(
        rules=RulesAdapter(RuleRegistry(), definitions),
        scorer=ScorerAdapter(components.scorer, pointer, was_canary=was_canary),
        explainer=ExplainerAdapter(components.explainer, components.cache, pointer),
        retriever=RetrieverAdapter(components.retriever),
        drafter=drafter,
        store=store,
        emit=emit,
        risk_policy=risk_policy,
        rag_top_k=settings.investigation_rag_top_k,
    )

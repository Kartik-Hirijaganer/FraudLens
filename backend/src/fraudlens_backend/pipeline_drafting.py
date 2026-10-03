"""Summary: Assemble replayable workflow drafters independently from deterministic scoring.

Key classes:
- DeferredUploadDrafter: perform egress and optional drafting admission after scoring.

Key functions:
- build_workflow_drafter: construct the existing single-writer or multi-agent workflow.

Notes:
- Uploads always retain their original source and cannot bypass live egress via fallback.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.agents.contracts import AgentExecutionRecord
from fraudlens_backend.agents.resume import AgentExecutionReplay
from fraudlens_backend.agents.runtime import AgentBudgetExceededError
from fraudlens_backend.db.repositories import (
    AgentExecutionRepository,
    DashboardRepository,
    SarDraftRepository,
    TransactionRepository,
    load_llm_daily_budget_usd,
)
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.pipeline_ports import RetrieverAdapter
from fraudlens_backend.sar.budget import SarBudgetExceededError
from fraudlens_backend.sar.drafter_fallback import LiveAgentFallbackDrafter
from fraudlens_backend.sar.drafter_replay import PersistedSarDrafter, resume_drafter
from fraudlens_backend.sar.egress import EgressBlockedError, load_egress_policy, project_for_model
from fraudlens_backend.settings import AppSettings
from fraudlens_ml.sar import (
    SarDrafter,
    SarDraftResult,
    SarDraftStatus,
    SarEventType,
    SarInput,
    SarStreamEvent,
)

if TYPE_CHECKING:
    from fraudlens_backend.pipeline_wiring import PipelineComponents


class DeferredUploadDrafter:
    """Delay optional dependency construction; preserve refusals as saved SAR outcomes."""

    def __init__(self, build: Callable[[], Awaitable[SarDrafter]], *, live: bool) -> None:
        self._build = build
        self._live = live

    async def draft(self, sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        """Check source before admission or provider construction; do not fall back on refusal."""
        try:
            if self._live:
                project_for_model(sar_input, load_egress_policy())
            drafter = await self._build()
            async for event in drafter.draft(sar_input):
                yield event
        except (
            EgressBlockedError,
            AgentBudgetExceededError,
            SarBudgetExceededError,
            AppError,
        ) as exc:
            code = (
                exc.code
                if isinstance(exc, (EgressBlockedError, AppError))
                else "llm_budget_exceeded"
            )
            yield SarStreamEvent(
                type=SarEventType.FAILED,
                result=SarDraftResult(
                    status=SarDraftStatus.FAILED,
                    model_id="unavailable",
                    prompt_version="unavailable",
                    prompt_hash="unavailable",
                    error_code=code,
                ),
            )


async def build_workflow_drafter(  # noqa: PLR0913 - explicit identity and policy dependencies.
    *,
    components: PipelineComponents,
    session: AsyncSession,
    settings: AppSettings,
    agency_id: uuid.UUID,
    run_id: uuid.UUID,
    transaction_id: uuid.UUID,
    sessionmaker: async_sessionmaker[AsyncSession] | None,
    workflow_mode: str,
    log_call: Callable[..., None],
) -> SarDrafter:
    """Build the existing drafting workflow with its budget checks and durable replay."""
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
            log_call(
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
            daily_limit = await load_llm_daily_budget_usd(
                session, agency_id=agency_id, ceiling_usd=settings.llm_daily_budget_usd
            )
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
    return drafter

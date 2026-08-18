"""Summary: The backend's dependency-injection seam for the LangGraph investigation pipeline
(plan §16 Phase 8, ADR-016). It is where the BACKEND wires the heavy ML + the LLM drafter into the
pipeline that — by layering — depends only on protocols (`fraudlens-ml` never imports the backend or
`fraudlens-llm`). The adapters map the real `Scorer` / `Explainer` / `Retriever` and the injected
`SarDrafter` onto the pipeline's light ports; `PipelineRunStore` implements the async `RunStore`
over the agency-scoped repositories (committing incrementally so a mid-pipeline failure still
leaves the partial event log + deterministic-core result durable); `RunManager` is the in-process
run registry that `POST /investigations` owns the run through — it dedupes by `Idempotency-Key`,
launches the `Runner` as a background task on its OWN session (so the run completes regardless of
any stream), and fans the Runner's live events out to SSE subscribers (the queue-ready seam to a
    future worker). Durable idempotency lives on `analysis_runs`; this manager retains only active
    process-local execution and subscriber state. The risk policy is resolved from `system_config`
    with the core `RiskPolicy()` as
the safe cached default (plan §9.1), and the same-account history feeding rules + features is
loaded windowed.

Key classes:
- RulesAdapter: adapts the pure `RuleRegistry` + the agency's rule set onto `RulesPort`.
- ScorerAdapter: adapts the heavy `Scorer` (+ the routed active/canary pointer) onto `ScorerPort`.
- ExplainerAdapter: adapts the heavy SHAP `Explainer` (+ cache) onto `ExplainerPort`.
- RetrieverAdapter: adapts the heavy `Retriever` (+ citation fencing) onto `RetrieverPort`.
- PipelineRunStore: the async `RunStore` over the analysis/registry/SAR repositories (commits).
- PipelineComponents: the process-wide heavy singletons (model cache, scorer, explainer, retriever).
- RunManager: the in-process active-run registry — background-task launch and SSE pub/sub.

Key functions:
- build_pipeline_components: construct the process-wide singletons from settings + the index dir.
- resolve_workflow_mode: apply the settings-and-tenant feature gate, failing closed.
- load_risk_policy: resolve the `RiskPolicy` from `system_config` (safe core defaults on any miss).
- build_pipeline_input: assemble the PHI-free `PipelineInput` (context + same-account history).
- resolve_scoring_pointer: route to an override, active/canary, or gated dev candidate fallback.
- build_pipeline_deps: resolve the routed pointer/rules/policy and assemble the `PipelineDeps`.

Notes:
- The scorer/explainer adapters raise when no active model deployment exists; that surfaces as a
  deterministic-core failure → `run.failed` unless the explicit non-production candidate fallback
  resolves a model (a healthy deploy is gated by `/readyz`, plan §10.6).
- During a canary rollout `resolve_scoring_pointer` routes ~canary_percent% of transactions (by a
  stable hash of the transaction id) to the candidate; the scorer + explainer share that one routed
  pointer so the SAME model both scores and explains, and the inference log records which arm ran
  (`was_canary`) — that is the "canary logs both models" of plan §5.4 (Phase 10, §10.5).
- Retrieval and ingestion use the same config-selected embedder factory. Hashing remains the
  deterministic default; live mode uses the backend's guardrailed OpenRouter adapter.
- `RunManager` evicts a finished run's record once no subscriber remains; an SSE observer that
  connects after eviction replays from the persisted `analysis_run_events` (DB).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.agents.contracts import AgentExecutionRecord
from fraudlens_backend.agents.resume import AgentExecutionReplay
from fraudlens_backend.db.models import SystemConfig, Transaction
from fraudlens_backend.db.models.enums import AnalysisRunEventType, Severity
from fraudlens_backend.db.repositories import (
    AgentExecutionRepository,
    AnalysisRunRepository,
    DashboardRepository,
    ModelRegistryRepository,
    RuleRepository,
    SarDraftRepository,
    TransactionRepository,
    load_feature_flags,
    load_llm_daily_budget_usd,
)
from fraudlens_backend.db.repositories.alerts import compute_review_flags
from fraudlens_backend.middleware.logging import APP_LOGGER_NAME, get_logger
from fraudlens_backend.rag import build_embedder
from fraudlens_backend.sar import build_sar_drafter
from fraudlens_backend.sar.drafter_fallback import LiveAgentFallbackDrafter
from fraudlens_backend.sar.factory import AgentDrafterFactory, build_agent_drafter_factory
from fraudlens_backend.settings import AppSettings, find_config_dir
from fraudlens_backend.telemetry import log_llm_call
from fraudlens_core import (
    RiskBand,
    RiskPolicy,
    RuleContext,
    RuleEvaluation,
    RuleRegistry,
    RuleTransaction,
    TransactionDirection,
)
from fraudlens_ml.pipeline import (
    AlertRecord,
    EventEmitter,
    InferenceRecord,
    PipelineDeps,
    PipelineEventType,
    PipelineInput,
    RagRecord,
    RagResult,
    ResultRecord,
    Runner,
    RunProvenance,
    ScoreResult,
    ShapResult,
    StreamMessage,
)
from fraudlens_ml.rag import Retriever, build_rag_context, extract_citations
from fraudlens_ml.sar import SarCitation, SarDrafter, SarDraftResult, SarFeature
from fraudlens_ml.scoring import (
    CanaryRouter,
    DeploymentPointer,
    Explainer,
    ModelCache,
    Scorer,
)

_RISK_BAND_THRESHOLDS_KEY = "riskBandThresholds"
_ALERT_THRESHOLD_KEY = "alertThreshold"
_RISK_BLEND_MODEL_WEIGHT_KEY = "riskBlendModelWeight"


def _anchored(path_value: str) -> Path:
    """Resolve a config path; a relative value anchors at the process CWD (repo root / /app)."""
    path = Path(path_value)
    return path if path.is_absolute() else Path.cwd() / path


def _config_anchored(path_value: str) -> Path:
    """Resolve a relative path below config/, rejecting absolute paths and traversal."""
    path = Path(path_value)
    if path.is_absolute():
        raise ValueError("Multi-agent configuration must be relative to the config directory")
    base = find_config_dir().resolve()
    resolved = (base / path).resolve()
    if not resolved.is_relative_to(base):
        raise ValueError("Multi-agent configuration must remain below the config directory")
    return resolved


# --------------------------------------------------------------------------------------------------
# Port adapters: map the real heavy implementations onto the pipeline's light protocols.
# --------------------------------------------------------------------------------------------------


class RulesAdapter:
    """Adapts the pure `RuleRegistry` + the agency's merged rule set onto `RulesPort`."""

    def __init__(self, registry: RuleRegistry, definitions: tuple[Any, ...]) -> None:
        """Bind the rules engine and the resolved (defaults < global < agency) definitions."""
        self._registry = registry
        self._definitions = definitions

    def evaluate(self, context: RuleContext) -> RuleEvaluation:
        """Evaluate the deterministic rules engine for the context (fault-isolated)."""
        return self._registry.evaluate(self._definitions, context)


class ScorerAdapter:
    """Adapts the heavy `Scorer` (+ the routed deployment pointer) onto `ScorerPort`."""

    def __init__(
        self, scorer: Scorer, pointer: DeploymentPointer | None, *, was_canary: bool = False
    ) -> None:
        """Bind the scorer, the resolved (active or canary-routed) pointer, and the canary flag."""
        self._scorer = scorer
        self._pointer = pointer
        self._was_canary = was_canary

    def score(self, context: RuleContext) -> ScoreResult:
        """Score via the routed model; raise when no deployment exists (→ run.failed).

        `was_canary` is the per-run routing decision (plan §10.5); it flows onto the `ScoreResult`
        so the scoring step's hash-only inference log records which arm scored ("logs both").
        """
        if self._pointer is None:
            raise RuntimeError("no active model deployment")
        output = self._scorer.score(self._pointer, context)
        return ScoreResult(
            fraud_probability=output.fraud_probability,
            model_version_label=output.model_version_label,
            was_canary=self._was_canary,
            risk_thresholds=output.risk_thresholds,
        )


class ExplainerAdapter:
    """Adapts the heavy SHAP `Explainer` (+ the model cache) onto `ExplainerPort`."""

    def __init__(
        self, explainer: Explainer, cache: ModelCache, pointer: DeploymentPointer | None
    ) -> None:
        """Bind the explainer, the artifact cache, and the active deployment pointer."""
        self._explainer = explainer
        self._cache = cache
        self._pointer = pointer

    def explain(self, context: RuleContext) -> ShapResult:
        """Explain the same model that scored; raise when no deployment exists (→ run.failed)."""
        if self._pointer is None:
            raise RuntimeError("no active model deployment")
        loaded = self._cache.get(self._pointer)
        explanation = self._explainer.explain(loaded, context)
        return ShapResult(
            base_value=explanation.base_value,
            shap_values=dict(explanation.shap_values),
            top_features=tuple(
                SarFeature(feature=item.feature, value=item.value, shap_value=item.shap_value)
                for item in explanation.top_features
            ),
        )


class RetrieverAdapter:
    """Adapts the heavy `Retriever` (+ citation extraction + fencing) onto `RetrieverPort`."""

    def __init__(self, retriever: Retriever) -> None:
        """Bind the FinCEN/BSA retriever the investigation cites from."""
        self._retriever = retriever

    def retrieve(self, query: str, *, top_k: int) -> RagResult:
        """Retrieve grounded citations + the escaped fenced context for the SAR prompt."""
        result = self._retriever.retrieve(query, top_k=top_k)
        citations = tuple(
            SarCitation(
                citation=item.citation, title=item.title, source=item.source, snippet=item.snippet
            )
            for item in extract_citations(result.chunks)
        )
        return RagResult(
            citations=citations,
            rag_context=build_rag_context(result.chunks),
            mode=result.mode,
            rag_version=result.rag_version,
            chunks=tuple(chunk.model_dump(mode="json") for chunk in result.chunks),
        )


# --------------------------------------------------------------------------------------------------
# Persistence: the async RunStore over the agency-scoped repositories (commits incrementally).
# --------------------------------------------------------------------------------------------------


class PipelineRunStore:
    """The async `RunStore` over the analysis/registry/SAR repositories (commits per write)."""

    def __init__(  # noqa: PLR0913 - run-scoped session + the repositories it persists through (keyword-only).
        self,
        *,
        session: AsyncSession,
        run_id: uuid.UUID,
        transaction_id: uuid.UUID,
        analysis: AnalysisRunRepository,
        registry: ModelRegistryRepository,
        sar: SarDraftRepository,
        review_low_confidence_margin: float = 0.1,
    ) -> None:
        """Bind the run-scoped session + repositories the pipeline persists through."""
        self._session = session
        self._run_id = run_id
        self._transaction_id = transaction_id
        self._analysis = analysis
        self._registry = registry
        self._sar = sar
        self._review_low_confidence_margin = review_low_confidence_margin

    async def append_event(self, event_type: PipelineEventType, payload: dict[str, Any]) -> int:
        """Persist the next ordered run event (mapping the pipeline type by value) + commit."""
        seq = await self._analysis.append_event(
            run_id=self._run_id,
            event_type=AnalysisRunEventType(event_type.value),
            payload=payload,
        )
        await self._session.commit()
        return seq

    async def save_result(self, record: ResultRecord) -> None:
        """Persist the immutable deterministic-core `analysis_results` snapshot + commit."""
        await self._analysis.save_result(
            run_id=self._run_id,
            fraud_probability=record.fraud_probability,
            shap_values=record.shap_values,
            top_features=record.top_features,
            rule_hits=record.rule_hits,
            combined_score=record.combined_score,
            risk_band=record.risk_band,
            model_version=record.model_version,
        )
        await self._session.commit()

    async def log_inference(self, record: InferenceRecord) -> None:
        """Resolve the scored label to its registry id and persist the hash-only inference log."""
        version = await self._registry.get_version_by_label(record.model_version_label)
        if version is None:  # an unregistered label cannot be hash-logged; skip (best-effort)
            return
        await self._analysis.log_inference(
            run_id=self._run_id,
            model_version_id=version.id,
            was_canary=record.was_canary,
            fraud_probability=record.fraud_probability,
            feature_hash=record.feature_hash,
        )
        await self._session.commit()

    async def save_rag(self, record: RagRecord) -> None:
        """Persist the `rag_retrievals` row for the run + commit."""
        await self._analysis.save_retrieval(
            run_id=self._run_id,
            query=record.query,
            top_k=record.top_k,
            chunks=record.chunks,
            rag_version=record.rag_version,
        )
        await self._session.commit()

    async def save_sar(self, result: SarDraftResult) -> str:
        """Persist the SAR draft (draft or failed) for the run + commit; return its id.

        Single-writer drafts emit one PHI-free aggregate cost/usage event after persistence.
        Multi-agent attempts emit their own latency-populated events before this aggregate step,
        avoiding duplicate telemetry. No event contains prompt content, and background run/tenant
        identifiers are passed explicitly because request contextvars are unavailable.
        """
        draft = await self._sar.create_from_result(run_id=self._run_id, result=result)
        analysis_result = await self._analysis.get_result(self._run_id)
        if analysis_result is not None:
            review_flags = compute_review_flags(
                risk_band=analysis_result.risk_band,
                fraud_probability=analysis_result.fraud_probability,
                sar_status=result.status.value,
                low_confidence_margin=self._review_low_confidence_margin,
            )
            await self._analysis.update_alert_review_flags(
                run_id=self._run_id,
                review_flags=review_flags,
            )
        await self._session.commit()
        if result.workflow != "multi_agent":
            log_llm_call(
                model=result.model_id,
                prompt_version=result.prompt_version,
                prompt_hash=result.prompt_hash,
                input_tokens=result.token_usage.input_tokens,
                output_tokens=result.token_usage.output_tokens,
                total_tokens=result.token_usage.total_tokens,
                cost_usd=result.cost_usd,
                fallback_count=result.fallback_count,
                cached=result.cached,
                run_id=str(self._run_id),
                agency_id=str(self._sar.agency_id),
            )
        return str(draft.id)

    async def raise_alert(self, record: AlertRecord) -> None:
        """Persist the conditional alert before RAG/SAR enrichment begins, then commit.

        Initial flags use the persisted deterministic result (and any pre-existing draft on a
        resumed run). `save_sar` refreshes them after enrichment so a failed SAR adds the existing
        manual-review flag without delaying alert creation behind an LLM call.
        """
        result = await self._analysis.get_result(self._run_id)
        sar = await self._sar.get_for_run(self._run_id)
        review_flags = compute_review_flags(
            risk_band=record.risk_band,
            fraud_probability=result.fraud_probability if result is not None else None,
            sar_status=sar.status.value if sar is not None else None,
            low_confidence_margin=self._review_low_confidence_margin,
        )
        await self._analysis.raise_alert(
            run_id=self._run_id,
            transaction_id=self._transaction_id,
            severity=Severity(record.severity),
            review_flags=review_flags,
        )
        await self._session.commit()

    async def complete_run(
        self, *, combined_score: float, risk_band: RiskBand, provenance: RunProvenance
    ) -> None:
        """Mark the run completed, stamp provenance + the transaction's latest run + commit."""
        await self._analysis.complete(
            run_id=self._run_id,
            combined_score=combined_score,
            risk_band=risk_band,
            model_version=provenance.model_version,
            rules_version=provenance.rules_version,
            rag_version=provenance.rag_version,
            prompt_version=provenance.prompt_version,
        )
        await self._session.commit()

    async def fail_run(self, *, error_code: str, provenance: RunProvenance) -> None:
        """Mark the run failed with the stable error code (+ known partial provenance) + commit."""
        await self._analysis.fail(
            run_id=self._run_id,
            error_code=error_code,
            model_version=provenance.model_version,
            rules_version=provenance.rules_version,
        )
        await self._session.commit()


# --------------------------------------------------------------------------------------------------
# Process-wide components + per-run dependency assembly.
# --------------------------------------------------------------------------------------------------


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


async def resolve_workflow_mode(
    session: AsyncSession,
    *,
    settings: AppSettings,
    agency_id: uuid.UUID,
    requested: str | None = None,
) -> str:
    """Resolve workflow selection through settings AND tenant flags, failing closed."""
    flags = await load_feature_flags(session, agency_id=agency_id)
    enabled = settings.multi_agent_sar_enabled and flags.multi_agent_sar
    if requested == "single_writer":
        return "single_writer"
    return "multi_agent" if enabled else "single_writer"


async def load_risk_policy(session: AsyncSession) -> RiskPolicy:
    """Resolve the `RiskPolicy` from global `system_config`, falling back to the core defaults."""
    default = RiskPolicy()
    try:
        stmt = select(SystemConfig).where(
            SystemConfig.agency_id.is_(None),
            SystemConfig.key.in_(
                [
                    _RISK_BAND_THRESHOLDS_KEY,
                    _ALERT_THRESHOLD_KEY,
                    _RISK_BLEND_MODEL_WEIGHT_KEY,
                ]
            ),
        )
        rows = {row.key: row.value for row in (await session.execute(stmt)).scalars().all()}
    except Exception:  # DB hiccup → safe cached in-process defaults (plan §9.1)
        return default
    thresholds = _parse_thresholds(rows.get(_RISK_BAND_THRESHOLDS_KEY), default.band_thresholds)
    alert_threshold = _parse_float(rows.get(_ALERT_THRESHOLD_KEY), default.alert_threshold)
    model_weight = _parse_float(rows.get(_RISK_BLEND_MODEL_WEIGHT_KEY), default.model_weight)
    return RiskPolicy(
        model_weight=model_weight,
        band_thresholds=thresholds,
        alert_threshold=alert_threshold,
    )


def _parse_thresholds(raw: Any, fallback: dict[RiskBand, float]) -> dict[RiskBand, float]:
    """Parse a `{band: lower}` config map into typed thresholds (fallback on any bad value)."""
    if not isinstance(raw, dict):
        return dict(fallback)
    parsed: dict[RiskBand, float] = {}
    for key, value in raw.items():
        try:
            parsed[RiskBand(str(key))] = float(value)
        except (ValueError, TypeError):
            return dict(fallback)
    return parsed or dict(fallback)


def _parse_float(raw: Any, fallback: float) -> float:
    """Coerce a config value to float, falling back when absent or un-coercible."""
    try:
        return float(raw) if raw is not None else fallback
    except (ValueError, TypeError):
        return fallback


def _to_rule_transaction(transaction: Transaction, *, account: str) -> RuleTransaction:
    """Project a persisted transaction onto a PHI-free RuleTransaction with its direction."""
    direction = (
        TransactionDirection.OUTBOUND
        if transaction.origin_account == account
        else TransactionDirection.INBOUND
    )
    return RuleTransaction(
        amount=transaction.amount,
        currency=transaction.currency,
        country=transaction.country,
        channel=transaction.channel,
        occurred_at=transaction.occurred_at,
        direction=direction,
    )


async def build_pipeline_input(
    *,
    repo: TransactionRepository,
    transaction: Transaction,
    run_id: uuid.UUID,
    agency_id: uuid.UUID,
    settings: AppSettings,
) -> PipelineInput:
    """Assemble the PHI-free PipelineInput from a transaction + its windowed account histories.

    The origin-account history feeds the rules engine + feature extractor (each filters to its
    own window); the destination-account history (directions relative to the destination) feeds
    the v2 counterparty fan-in features only — both use the same window/cap settings so training
    can mirror exactly what scoring sees.
    """
    history_rows = await repo.same_account_history(
        account=transaction.origin_account,
        before=transaction.occurred_at,
        window_hours=settings.investigation_history_window_hours,
        limit=settings.investigation_history_max,
    )
    history = tuple(
        _to_rule_transaction(row, account=transaction.origin_account) for row in history_rows
    )
    counterparty_rows = await repo.same_account_history(
        account=transaction.dest_account,
        before=transaction.occurred_at,
        window_hours=settings.investigation_history_window_hours,
        limit=settings.investigation_history_max,
    )
    counterparty_history = tuple(
        _to_rule_transaction(row, account=transaction.dest_account) for row in counterparty_rows
    )
    current = RuleTransaction(
        amount=transaction.amount,
        currency=transaction.currency,
        country=transaction.country,
        channel=transaction.channel,
        occurred_at=transaction.occurred_at,
        direction=TransactionDirection.OUTBOUND,
    )
    return PipelineInput(
        agency_id=str(agency_id),
        run_id=str(run_id),
        transaction_id=str(transaction.id),
        rule_context=RuleContext(
            transaction=current,
            history=history,
            counterparty_history=counterparty_history,
        ),
        amount=transaction.amount,
        currency=transaction.currency,
        country=transaction.country,
        channel=transaction.channel,
        feature_hash=transaction.feature_hash,
    )


async def resolve_scoring_pointer(
    registry: ModelRegistryRepository,
    *,
    routing_key: str,
    model_override: str | None = None,
    allow_candidate_fallback: bool = False,
) -> tuple[DeploymentPointer | None, bool]:
    """Resolve the per-run scoring pointer + whether it routed to the canary (plan §10.5 / §5.4).

    `model_override` (a registered version label) takes precedence over everything: the run scores
    with exactly that version (the active model is its last-known-good fallback) and `was_canary` is
    False — it is an explicit operator choice, not a canary-routing decision. Absent: with no canary
    configured (or 0% / unresolved) this is the active pointer (+ previous active for fallback,
    unchanged from v1); when a canary rollout is live, `CanaryRouter` decides by a stable hash of
    `routing_key` (the transaction id) whether this run scores with the canary (its inference log
    then records the canary arm). Routing is deterministic, so a re-run / replay routes identically.
    """
    pointer = await registry.build_pointer()
    if model_override is not None:
        version = await registry.get_version_by_label(model_override)
        if version is None:  # the API validates existence first; defensive fallthrough to active
            return pointer, False
        overridden = DeploymentPointer(
            active_version_label=version.version_label,
            active_artifact_uri=version.artifact_uri,
            previous_version_label=pointer.active_version_label if pointer is not None else None,
            previous_artifact_uri=pointer.active_artifact_uri if pointer is not None else None,
        )
        return overridden, False
    if pointer is None:
        candidate = (
            await registry.build_latest_candidate_pointer() if allow_candidate_fallback else None
        )
        return candidate, False
    canary = await registry.build_canary_deployment()
    if canary is None:
        return pointer, False
    decision = CanaryRouter().route(canary, routing_key)
    if not decision.was_canary:
        return pointer, False
    routed = DeploymentPointer(
        active_version_label=decision.version_label,
        active_artifact_uri=decision.artifact_uri,
        previous_version_label=canary.active_version_label,
        previous_artifact_uri=canary.active_artifact_uri,
    )
    return routed, True


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
    )
    drafter = components.drafter
    if workflow_mode == "multi_agent":
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


# --------------------------------------------------------------------------------------------------
# RunManager: the in-process run registry POST owns the run through (ADR-016).
# --------------------------------------------------------------------------------------------------


@dataclass
class _RunState:
    """In-process state for one active run: its live subscribers, done flag, and driving task."""

    subscribers: set[asyncio.Queue[StreamMessage | None]] = field(default_factory=set)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None


class RunManager:
    """In-process active-run registry for background launch and SSE pub/sub."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        components: PipelineComponents,
        settings: AppSettings,
    ) -> None:
        """Bind the sessionmaker (for background sessions), the components, and settings."""
        self._sessionmaker = sessionmaker
        self._components = components
        self._settings = settings
        self._runs: dict[str, _RunState] = {}

    @property
    def agent_quotas(self) -> Any:
        """Return the validated live multi-agent quota configuration."""
        return self._components.agent_config.quotas

    @property
    def agent_graph_version(self) -> str:
        """Return the validated graph version persisted on multi-agent runs."""
        return str(self._components.agent_config.graph_version)

    async def ensure_agent_budget(self, session: AsyncSession, *, agency_id: uuid.UUID) -> None:
        """Reject a live graph whose worst-case charge would cross the tenant daily budget."""
        if self._settings.llm_mode != "live":
            return
        limit = await load_llm_daily_budget_usd(session, agency_id=agency_id)
        spent = await DashboardRepository(session, agency_id).sar_cost_today(
            as_of=datetime.now(UTC)
        )
        if spent + self._components.agent_max_cost_usd > limit:
            from fraudlens_backend.models.errors import AppError  # noqa: PLC0415

            raise AppError("llm_budget_exceeded")

    def start(  # noqa: PLR0913 - explicit persisted run identity and selected workflow.
        self,
        *,
        agency_id: uuid.UUID,
        run_id: uuid.UUID,
        transaction_id: uuid.UUID,
        pipeline_input: PipelineInput,
        model_override: str | None = None,
        workflow_mode: str = "single_writer",
    ) -> None:
        """Launch the Runner as a background task that owns the run (independent of any stream)."""
        state = _RunState()
        self._runs[str(run_id)] = state
        state.task = asyncio.create_task(
            self._drive(
                agency_id=agency_id,
                run_id=run_id,
                transaction_id=transaction_id,
                pipeline_input=pipeline_input,
                state=state,
                model_override=model_override,
                workflow_mode=workflow_mode,
            )
        )

    async def _drive(  # noqa: PLR0913 - the run's identity + input + state + the optional override (keyword-only).
        self,
        *,
        agency_id: uuid.UUID,
        run_id: uuid.UUID,
        transaction_id: uuid.UUID,
        pipeline_input: PipelineInput,
        state: _RunState,
        model_override: str | None = None,
        workflow_mode: str = "single_writer",
    ) -> None:
        """Run the pipeline to completion on a fresh session, then signal + evict the run state."""
        try:
            async with self._sessionmaker() as session:
                deps = await build_pipeline_deps(
                    components=self._components,
                    session=session,
                    settings=self._settings,
                    agency_id=agency_id,
                    run_id=run_id,
                    transaction_id=transaction_id,
                    emit=self._emitter(state),
                    model_override=model_override,
                    sessionmaker=self._sessionmaker,
                    workflow_mode=workflow_mode,
                )
                await Runner(deps).run(pipeline_input)
        except (
            Exception
        ):  # a background run must never crash the worker silently (logged, PHI-free)
            get_logger(APP_LOGGER_NAME).error(
                "investigation.run_error", run_id=str(run_id), exc_info=True
            )
        finally:
            state.done.set()
            self._broadcast(state, None)
            if not state.subscribers:
                self._runs.pop(str(run_id), None)

    def _emitter(self, state: _RunState) -> EventEmitter:
        """Return an EventEmitter that fans a StreamMessage out to the run's live subscribers."""

        async def emit(message: StreamMessage) -> None:
            self._broadcast(state, message)

        return emit

    @staticmethod
    def _broadcast(state: _RunState, message: StreamMessage | None) -> None:
        """Put a message (or the None done-sentinel) on every current subscriber queue."""
        for queue in tuple(state.subscribers):
            queue.put_nowait(message)

    def attach(self, run_id: str) -> asyncio.Queue[StreamMessage | None] | None:
        """Subscribe a fresh live queue to an active run, or None when no record exists (replay)."""
        state = self._runs.get(run_id)
        if state is None:
            return None
        queue: asyncio.Queue[StreamMessage | None] = asyncio.Queue()
        state.subscribers.add(queue)
        return queue

    def detach(self, run_id: str, queue: asyncio.Queue[StreamMessage | None]) -> None:
        """Unsubscribe a live queue; evict a finished run's state once no subscriber remains."""
        state = self._runs.get(run_id)
        if state is None:
            return
        state.subscribers.discard(queue)
        if state.done.is_set() and not state.subscribers:
            self._runs.pop(run_id, None)

    async def join(self, run_id: str) -> None:
        """Await an in-flight run's background task to completion (graceful shutdown / tests)."""
        state = self._runs.get(run_id)
        if state is not None and state.task is not None:
            await state.task

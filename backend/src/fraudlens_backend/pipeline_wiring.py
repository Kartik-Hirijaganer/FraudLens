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
future worker). The risk policy is resolved from `system_config` with the core `RiskPolicy()` as
the safe cached default (plan §9.1), and the same-account history feeding rules + features is
loaded windowed.

Key classes:
- RulesAdapter: adapts the pure `RuleRegistry` + the agency's rule set onto `RulesPort`.
- ScorerAdapter: adapts the heavy `Scorer` (+ active pointer) onto `ScorerPort`.
- ExplainerAdapter: adapts the heavy SHAP `Explainer` (+ cache) onto `ExplainerPort`.
- RetrieverAdapter: adapts the heavy `Retriever` (+ citation fencing) onto `RetrieverPort`.
- PipelineRunStore: the async `RunStore` over the analysis/registry/SAR repositories (commits).
- PipelineComponents: the process-wide heavy singletons (model cache, scorer, explainer, retriever).
- RunManager: the in-process run registry — idempotency, background-task launch, and SSE pub/sub.

Key functions:
- build_pipeline_components: construct the process-wide singletons from settings + the index dir.
- load_risk_policy: resolve the `RiskPolicy` from `system_config` (safe core defaults on any miss).
- build_pipeline_input: assemble the PHI-free `PipelineInput` (context + same-account history).
- build_pipeline_deps: resolve the pointer/rules/policy and assemble the per-run `PipelineDeps`.

Notes:
- The scorer/explainer adapters raise when no active model deployment exists; that surfaces as a
  deterministic-core failure → `run.failed` (a healthy deploy is gated by `/readyz`, plan §10.6).
- The retriever uses the offline `HashingEmbedder` so it matches the keyless index the build bakes
  (`scripts/ingest_rag.py`), keeping investigations offline + deterministic in local-demo + tests.
- `RunManager` evicts a finished run's record once no subscriber remains, and LRU-bounds the
  Idempotency-Key→runId map, so neither grows without limit; an SSE observer that connects after
  eviction replays from the persisted `analysis_run_events` (DB).
"""

from __future__ import annotations

import asyncio
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.db.models import SystemConfig, Transaction
from fraudlens_backend.db.models.enums import AnalysisRunEventType, Severity
from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    ModelRegistryRepository,
    RuleRepository,
    SarDraftRepository,
    TransactionRepository,
)
from fraudlens_backend.db.repositories.alerts import compute_review_flags
from fraudlens_backend.middleware.logging import APP_LOGGER_NAME, get_logger
from fraudlens_backend.sar import build_sar_drafter
from fraudlens_backend.settings import AppSettings
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
from fraudlens_ml.rag import HashingEmbedder, Retriever, build_rag_context, extract_citations
from fraudlens_ml.sar import SarCitation, SarDrafter, SarDraftResult, SarFeature
from fraudlens_ml.scoring import DeploymentPointer, Explainer, ModelCache, Scorer

_RISK_BAND_THRESHOLDS_KEY = "riskBandThresholds"
_ALERT_THRESHOLD_KEY = "alertThreshold"


def _anchored(path_value: str) -> Path:
    """Resolve a config path; a relative value anchors at the process CWD (repo root / /app)."""
    path = Path(path_value)
    return path if path.is_absolute() else Path.cwd() / path


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
    """Adapts the heavy `Scorer` (+ the active deployment pointer) onto `ScorerPort`."""

    def __init__(self, scorer: Scorer, pointer: DeploymentPointer | None) -> None:
        """Bind the scorer and the resolved active/last-known-good deployment pointer."""
        self._scorer = scorer
        self._pointer = pointer

    def score(self, context: RuleContext) -> ScoreResult:
        """Score via the active model; raise when no deployment exists (→ run.failed)."""
        if self._pointer is None:
            raise RuntimeError("no active model deployment")
        output = self._scorer.score(self._pointer, context)
        return ScoreResult(
            fraud_probability=output.fraud_probability,
            model_version_label=output.model_version_label,
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
        """Persist the SAR draft (draft or failed) for the run + commit; return its id."""
        draft = await self._sar.create_from_result(run_id=self._run_id, result=result)
        await self._session.commit()
        return str(draft.id)

    async def raise_alert(self, record: AlertRecord) -> None:
        """Persist the conditional open `alerts` row (with review flags) for the run + commit.

        Review flags are derived from the already-persisted result + SAR + the run's band (the SAR
        and result are committed by their pipeline steps before the alert is raised), so a critical
        band, a low-confidence probability, or a failed SAR force-flags the alert for review (§8.5).
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


def build_pipeline_components(settings: AppSettings) -> PipelineComponents:
    """Construct the process-wide pipeline singletons from settings (paths anchored at the CWD)."""
    cache = ModelCache(_anchored(settings.model_artifacts_dir))
    retriever = Retriever(
        persist_dir=_anchored(settings.rag_index_dir),
        collection=settings.rag_collection,
        embedder=HashingEmbedder(),
        rag_version=settings.rag_version,
    )
    return PipelineComponents(
        cache=cache,
        scorer=Scorer(cache),
        explainer=Explainer(),
        retriever=retriever,
        drafter=build_sar_drafter(settings),
    )


async def load_risk_policy(session: AsyncSession) -> RiskPolicy:
    """Resolve the `RiskPolicy` from global `system_config`, falling back to the core defaults."""
    default = RiskPolicy()
    try:
        stmt = select(SystemConfig).where(
            SystemConfig.agency_id.is_(None),
            SystemConfig.key.in_([_RISK_BAND_THRESHOLDS_KEY, _ALERT_THRESHOLD_KEY]),
        )
        rows = {row.key: row.value for row in (await session.execute(stmt)).scalars().all()}
    except Exception:  # DB hiccup → safe cached in-process defaults (plan §9.1)
        return default
    thresholds = _parse_thresholds(rows.get(_RISK_BAND_THRESHOLDS_KEY), default.band_thresholds)
    alert_threshold = _parse_float(rows.get(_ALERT_THRESHOLD_KEY), default.alert_threshold)
    return RiskPolicy(band_thresholds=thresholds, alert_threshold=alert_threshold)


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
    """Assemble the PHI-free PipelineInput from a transaction + its windowed same-account history.

    The history feeds the rules engine + feature extractor (each filters to its own window).
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
        rule_context=RuleContext(transaction=current, history=history),
        amount=transaction.amount,
        currency=transaction.currency,
        country=transaction.country,
        channel=transaction.channel,
        feature_hash=transaction.feature_hash,
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
) -> PipelineDeps:
    """Resolve the pointer/rule-set/policy and assemble the per-run PipelineDeps for the Runner."""
    registry = ModelRegistryRepository(session)
    pointer = await registry.build_pointer()
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
    return PipelineDeps(
        rules=RulesAdapter(RuleRegistry(), definitions),
        scorer=ScorerAdapter(components.scorer, pointer),
        explainer=ExplainerAdapter(components.explainer, components.cache, pointer),
        retriever=RetrieverAdapter(components.retriever),
        drafter=components.drafter,
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
    """In-process run registry: idempotency dedupe, background-task launch, and SSE pub/sub."""

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
        # LRU-bounded so a long-lived single replica cannot grow the dedupe map without limit
        # (the cross-replica/restart dedupe is the deferred queue-ready seam, ADR-016 / §21).
        self._idempotency: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._idempotency_cap = settings.investigation_idempotency_cache_size
        self.lock = asyncio.Lock()

    def lookup_idempotent(self, agency_id: str, key: str) -> str | None:
        """Return the run id a prior request with this agency + Idempotency-Key created, if any."""
        run_id = self._idempotency.get((agency_id, key))
        if run_id is not None:
            self._idempotency.move_to_end((agency_id, key))  # mark recently used (LRU)
        return run_id

    def remember_idempotent(self, agency_id: str, key: str, run_id: str) -> None:
        """Record the run id for an agency + Idempotency-Key (double-click dedupe; LRU-bounded)."""
        self._idempotency[(agency_id, key)] = run_id
        self._idempotency.move_to_end((agency_id, key))
        while len(self._idempotency) > self._idempotency_cap:
            self._idempotency.popitem(last=False)  # evict the least-recently-used entry

    def start(
        self,
        *,
        agency_id: uuid.UUID,
        run_id: uuid.UUID,
        transaction_id: uuid.UUID,
        pipeline_input: PipelineInput,
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
            )
        )

    async def _drive(
        self,
        *,
        agency_id: uuid.UUID,
        run_id: uuid.UUID,
        transaction_id: uuid.UUID,
        pipeline_input: PipelineInput,
        state: _RunState,
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

"""Summary: Durable pipeline persistence and process-local background-run coordination.

Key classes:
- PipelineRunStore: persist each pipeline artifact through tenant-scoped repositories.
- RunManager: launch background runs and fan events out to subscribers.

Key functions:
- (none)

Notes:
- Pipeline dependency assembly is imported lazily to avoid a facade cycle.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.db.models.enums import AnalysisRunEventType, SarStatus, Severity
from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    ModelRegistryRepository,
    SarDraftRepository,
)
from fraudlens_backend.db.repositories.alerts import compute_review_flags
from fraudlens_backend.middleware.logging import APP_LOGGER_NAME, get_logger
from fraudlens_backend.settings import AppSettings
from fraudlens_backend.telemetry import log_llm_call
from fraudlens_core import RiskBand
from fraudlens_ml.pipeline import (
    AlertRecord,
    EventEmitter,
    InferenceRecord,
    PipelineEventType,
    PipelineInput,
    RagRecord,
    ResultRecord,
    Runner,
    RunProvenance,
    StreamMessage,
)
from fraudlens_ml.sar import SarDraftResult

if TYPE_CHECKING:
    from fraudlens_backend.pipeline_wiring import PipelineComponents

_RESTART_SINGLETON_EVENTS = frozenset(
    {
        PipelineEventType.RUN_STARTED,
        PipelineEventType.STEP_RULES_COMPLETED,
        PipelineEventType.STEP_SCORING_COMPLETED,
        PipelineEventType.STEP_SHAP_COMPLETED,
        PipelineEventType.STEP_RAG_COMPLETED,
        PipelineEventType.SAR_STARTED,
    }
)
_DEFERRED_EVENT_COMMITS = frozenset({PipelineEventType.STEP_SHAP_COMPLETED})


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
        lease_owner: str | None = None,
        fencing_token: int | None = None,
    ) -> None:
        """Bind the run-scoped session + repositories the pipeline persists through."""
        self._session = session
        self._run_id = run_id
        self._transaction_id = transaction_id
        self._analysis = analysis
        self._registry = registry
        self._sar = sar
        self._review_low_confidence_margin = review_low_confidence_margin
        self._lease_owner = lease_owner
        self._fencing_token = fencing_token

    async def _require_fence(self) -> None:
        """Reject a stale worker before it mutates any run-owned record."""
        await self._analysis.require_fence(
            run_id=self._run_id,
            lease_owner=self._lease_owner,
            fencing_token=self._fencing_token,
        )

    async def append_event(self, event_type: PipelineEventType, payload: dict[str, Any]) -> int:
        """Persist the next ordered run event (mapping the pipeline type by value) + commit."""
        await self._require_fence()
        persisted_type = AnalysisRunEventType(event_type.value)
        if self._lease_owner is not None and event_type in _RESTART_SINGLETON_EVENTS:
            existing = await self._analysis.event_by_type(
                run_id=self._run_id,
                event_type=persisted_type,
            )
            if existing is not None:
                return existing.seq
        seq = await self._analysis.append_event(
            run_id=self._run_id,
            event_type=persisted_type,
            payload=payload,
        )
        if event_type in {PipelineEventType.RUN_COMPLETED, PipelineEventType.RUN_FAILED}:
            await self._analysis.release_lease(run_id=self._run_id)
        if self._lease_owner is None or event_type not in _DEFERRED_EVENT_COMMITS:
            await self._session.commit()
        return seq

    async def save_result(self, record: ResultRecord) -> None:
        """Persist the immutable deterministic-core `analysis_results` snapshot + commit."""
        await self._require_fence()
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
        await self._require_fence()
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
        if self._lease_owner is None:
            await self._session.commit()

    async def save_rag(self, record: RagRecord) -> None:
        """Persist the `rag_retrievals` row for the run + commit."""
        await self._require_fence()
        await self._analysis.save_retrieval(
            run_id=self._run_id,
            query=record.query,
            top_k=record.top_k,
            chunks=record.chunks,
            rag_version=record.rag_version,
        )
        if self._lease_owner is None:
            await self._session.commit()

    async def save_sar(self, result: SarDraftResult) -> str:
        """Persist the SAR draft (draft or failed) for the run + commit; return its id.

        Single-writer drafts emit one PHI-free aggregate cost/usage event after persistence.
        Multi-agent attempts emit their own latency-populated events before this aggregate step,
        avoiding duplicate telemetry. No event contains prompt content, and background run/tenant
        identifiers are passed explicitly because request contextvars are unavailable.
        """
        await self._require_fence()
        existing = await self._sar.get_for_run(self._run_id)
        if (
            self._lease_owner is not None
            and existing is not None
            and existing.status is SarStatus.DRAFT
        ):
            return str(existing.id)
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
        await self._require_fence()
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
        await self._require_fence()
        await self._analysis.complete(
            run_id=self._run_id,
            combined_score=combined_score,
            risk_band=risk_band,
            model_version=provenance.model_version,
            rules_version=provenance.rules_version,
            rag_version=provenance.rag_version,
            prompt_version=provenance.prompt_version,
        )
        if self._lease_owner is None:
            await self._session.commit()

    async def fail_run(self, *, error_code: str, provenance: RunProvenance) -> None:
        """Mark the run failed with the stable error code (+ known partial provenance) + commit."""
        await self._session.rollback()
        await self._require_fence()
        await self._analysis.fail(
            run_id=self._run_id,
            error_code=error_code,
            model_version=provenance.model_version,
            rules_version=provenance.rules_version,
        )
        if self._lease_owner is None:
            await self._session.commit()


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

    @property
    def agent_max_cost_usd(self) -> Decimal:
        """Return the validated worst-case cost for one multi-agent attempt."""
        return self._components.agent_max_cost_usd

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
                from fraudlens_backend.pipeline_wiring import build_pipeline_deps  # noqa: PLC0415

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

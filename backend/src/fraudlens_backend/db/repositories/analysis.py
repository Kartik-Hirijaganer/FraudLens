"""Summary: The agency-scoped investigation-persistence repository (plan §9.1, §16 Phase 8).
Built on `TenantScopedRepository`, so every read/write is bound to one `agency_id` and a
cross-tenant run id resolves to nothing (no existence leak, plan §6.4). It is the single seam the
LangGraph pipeline's `RunStore` is implemented over: it CREATES the `analysis_runs` row the POST
handler owns, APPENDS the ordered `analysis_run_events` (the gap-free `seq` backing SSE replay,
ADR-016), and persists the immutable `analysis_results` snapshot, the `rag_retrievals` row, the
hash-only `model_inference_logs`, and the conditional open `alerts` row — then `complete`/`fail`
finalize the run and stamp the transaction's latest run + band. Every persisted payload is PHI-free
by construction (the pipeline assembles them from rule hits, SHAP feature names, escaped citations,
and structured non-PHI facts).

Key classes:
- AnalysisRunRepository: agency-scoped persistence for an investigation run + its child rows.

Key functions:
- (none)

Notes:
- `append_event` derives the next `seq` from `MAX(seq)+1` per run; the Runner appends serially
  (one in-process task per run), so the UNIQUE `(run_id, seq)` is honored without a sequence.
- `complete`/`fail` update the run in place and (on completion) the transaction's denormalized
  `latest_run_id` + `risk_band`; the transaction id is read from the agency-scoped run, not
  trusted from the caller (defense-in-depth tenant scoping).
- Writes flush but do NOT commit; the `RunStore` adapter commits incrementally so a mid-pipeline
  failure still leaves the partial event log + deterministic-core result durable (plan §10.6).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    Alert,
    AlertStatus,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunEvent,
    AnalysisRunEventType,
    ModelInferenceLog,
    RagRetrieval,
    RunStatus,
    Severity,
    Transaction,
)
from fraudlens_backend.db.repositories.base import TenantScopedRepository
from fraudlens_core import RiskBand


class AnalysisRunRepository(TenantScopedRepository[AnalysisRun]):
    """Agency-scoped persistence for an investigation run and its child rows."""

    def __init__(self, session: AsyncSession, agency_id: uuid.UUID) -> None:
        """Bind the session + agency scope to the `analysis_runs` table."""
        super().__init__(session, AnalysisRun, agency_id)

    async def create_running(
        self, *, transaction_id: uuid.UUID, triggered_by: uuid.UUID | None = None
    ) -> AnalysisRun:
        """Insert a new run in `running` status owned by `POST` and return it (flushed)."""
        run = AnalysisRun(
            agency_id=self._agency_id,
            transaction_id=transaction_id,
            status=RunStatus.RUNNING,
            triggered_by=triggered_by,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def append_event(
        self, *, run_id: uuid.UUID, event_type: AnalysisRunEventType, payload: dict[str, Any]
    ) -> int:
        """Append the next ordered run event (seq = MAX(seq)+1) and return its `seq`."""
        seq = await self._next_seq(run_id)
        self._session.add(
            AnalysisRunEvent(
                agency_id=self._agency_id,
                run_id=run_id,
                seq=seq,
                event_type=event_type,
                payload=payload,
            )
        )
        await self._session.flush()
        return seq

    async def get_result(self, run_id: uuid.UUID) -> AnalysisResult | None:
        """Return the immutable `analysis_results` snapshot for the run, or None (agency-scoped)."""
        stmt = select(AnalysisResult).where(
            AnalysisResult.agency_id == self._agency_id, AnalysisResult.run_id == run_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_retrieval(self, run_id: uuid.UUID) -> RagRetrieval | None:
        """Return the `rag_retrievals` row for the run, or None (agency-scoped)."""
        stmt = select(RagRetrieval).where(
            RagRetrieval.agency_id == self._agency_id, RagRetrieval.run_id == run_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def events_after(
        self, *, run_id: uuid.UUID, after_seq: int
    ) -> Sequence[AnalysisRunEvent]:
        """Return the run's persisted events with `seq > after_seq`, in order (SSE replay)."""
        stmt = (
            select(AnalysisRunEvent)
            .where(
                AnalysisRunEvent.agency_id == self._agency_id,
                AnalysisRunEvent.run_id == run_id,
                AnalysisRunEvent.seq > after_seq,
            )
            .order_by(AnalysisRunEvent.seq.asc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def save_result(  # noqa: PLR0913 - the immutable snapshot has many columns (keyword-only).
        self,
        *,
        run_id: uuid.UUID,
        fraud_probability: float,
        shap_values: dict[str, Any],
        top_features: list[dict[str, Any]],
        rule_hits: list[dict[str, Any]],
        combined_score: float,
        risk_band: RiskBand,
        model_version: str,
    ) -> None:
        """Persist the immutable `analysis_results` snapshot for the run (one per run)."""
        self._session.add(
            AnalysisResult(
                agency_id=self._agency_id,
                run_id=run_id,
                fraud_probability=fraud_probability,
                shap_values=shap_values,
                top_features=top_features,
                rule_hits=rule_hits,
                combined_score=combined_score,
                risk_band=risk_band,
                model_version=model_version,
            )
        )
        await self._session.flush()

    async def save_retrieval(
        self,
        *,
        run_id: uuid.UUID,
        query: str,
        top_k: int,
        chunks: list[dict[str, Any]],
        rag_version: str,
    ) -> None:
        """Persist the `rag_retrievals` row (the citations retrieved for the run)."""
        self._session.add(
            RagRetrieval(
                agency_id=self._agency_id,
                run_id=run_id,
                query=query,
                top_k=top_k,
                chunks=chunks,
                rag_version=rag_version,
            )
        )
        await self._session.flush()

    async def log_inference(
        self,
        *,
        run_id: uuid.UUID,
        model_version_id: uuid.UUID,
        was_canary: bool,
        fraud_probability: float,
        feature_hash: str,
    ) -> None:
        """Persist the hash-only `model_inference_logs` row for the scoring step (no PHI)."""
        self._session.add(
            ModelInferenceLog(
                agency_id=self._agency_id,
                run_id=run_id,
                model_version_id=model_version_id,
                was_canary=was_canary,
                fraud_probability=fraud_probability,
                feature_hash=feature_hash,
            )
        )
        await self._session.flush()

    async def raise_alert(
        self,
        *,
        run_id: uuid.UUID,
        transaction_id: uuid.UUID,
        severity: Severity,
        review_flags: list[dict[str, Any]] | None = None,
    ) -> Alert:
        """Insert an `alerts` row for the run (raised when the threshold is crossed).

        `review_flags` are the PHI-free force-review reasons computed at investigation time
        (critical band / low model confidence / SAR unavailable, plan §8.5, Phase 9).
        """
        flags = review_flags or []
        alert = Alert(
            agency_id=self._agency_id,
            transaction_id=transaction_id,
            run_id=run_id,
            status=AlertStatus.PENDING_REVIEW if flags else AlertStatus.OPEN,
            severity=severity,
            review_flags=flags,
        )
        self._session.add(alert)
        await self._session.flush()
        return alert

    async def complete(  # noqa: PLR0913 - the run carries per-step version provenance (keyword-only).
        self,
        *,
        run_id: uuid.UUID,
        combined_score: float,
        risk_band: RiskBand,
        model_version: str | None,
        rules_version: str | None,
        rag_version: str | None,
        prompt_version: str | None,
    ) -> None:
        """Mark the run completed, stamp its provenance, and update the transaction's latest run."""
        run = await self.get(run_id)
        if run is None:
            return
        run.status = RunStatus.COMPLETED
        run.risk_score = combined_score
        run.risk_band = risk_band
        run.model_version = model_version
        run.rules_version = rules_version
        run.rag_version = rag_version
        run.prompt_version = prompt_version
        # The transaction id comes from the agency-scoped run, so it belongs to this tenant.
        transaction = await self._session.get(Transaction, run.transaction_id)
        if transaction is not None:
            transaction.latest_run_id = run.id
            transaction.risk_band = risk_band
        await self._session.flush()

    async def fail(
        self,
        *,
        run_id: uuid.UUID,
        error_code: str,
        model_version: str | None = None,
        rules_version: str | None = None,
    ) -> None:
        """Mark the run failed with a stable error code (partial provenance when known)."""
        run = await self.get(run_id)
        if run is None:
            return
        run.status = RunStatus.FAILED
        run.error_code = error_code
        run.model_version = model_version
        run.rules_version = rules_version
        await self._session.flush()

    async def _next_seq(self, run_id: uuid.UUID) -> int:
        """Return the next monotonic event seq for a run (1 when none exist yet)."""
        stmt = select(func.max(AnalysisRunEvent.seq)).where(
            AnalysisRunEvent.agency_id == self._agency_id, AnalysisRunEvent.run_id == run_id
        )
        current = (await self._session.execute(stmt)).scalar_one_or_none()
        return (current or 0) + 1

"""Summary: The batch investigation job runner (plan §16 Phase 8: "batch job runner"). It drives
the SAME LangGraph pipeline the interactive `POST /investigations` path uses, but headlessly — no
SSE stream, no `RunManager` background task — over a set of transactions, then records a PHI-free
`job_executions(batch_score)` row for the ops audit trail. Each transaction gets its own persisted
`analysis_runs` row + full pipeline (rules→scoring→SHAP→RAG→SAR), reusing the warm
`PipelineComponents` (model cache + retriever + drafter) and the per-run `RunStore`, so a batch
sweep produces exactly the same durable results/events/alerts an interactive run would. Runs are
sequential on one session (the store commits incrementally), so a mid-batch failure leaves prior
runs durable. `main` is the dev/demo entry point that scores the demo agency's un-investigated
transactions; the Container Apps Jobs trigger that schedules it lands in Phase 14 (the
local-vs-cloud seam is `backends/jobs.py`).

Key classes:
- BatchScoreResult: the PHI-free outcome counts of a batch run (requested/completed/failed/skipped).

Key functions:
- run_batch_score: investigate a set of transactions sequentially + record a `job_executions` row.
- select_uninvestigated: the ids of an agency's not-yet-investigated transactions (capped).
- main: dev/demo entry point — build the engine + components and batch-score the demo agency.

Notes:
- The live `EventEmitter` is a no-op here (batch has no observers); the pipeline still persists each
  ordered event, so a batch-scored run is fully replayable via `GET /investigations/{runId}/stream`.
- `main` is dev/demo-oriented (like `scripts/seed.py`) and scores un-investigated transactions, so
  re-running it is naturally incremental.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import JobExecution, JobStatus, JobType, Transaction
from fraudlens_backend.db.repositories import AnalysisRunRepository, TransactionRepository
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.demo import DEMO_AGENCY_ID
from fraudlens_backend.pipeline_wiring import (
    PipelineComponents,
    build_pipeline_components,
    build_pipeline_deps,
    build_pipeline_input,
)
from fraudlens_backend.settings import AppSettings, get_settings
from fraudlens_ml.pipeline import Runner, StreamMessage

_DEFAULT_BATCH_LIMIT = 100


class BatchScoreResult(BaseModel):
    """The PHI-free outcome counts of a batch investigation run (recorded in `job_executions`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(..., description="The `job_executions(batch_score)` row id for this run.")
    requested: int = Field(..., ge=0, description="Transactions the batch was asked to score.")
    completed: int = Field(..., ge=0, description="Runs that completed (deterministic core ran).")
    failed: int = Field(..., ge=0, description="Runs that failed in the deterministic core.")
    skipped: int = Field(..., ge=0, description="Requested ids not found for the agency (skipped).")


async def _noop_emit(_message: StreamMessage) -> None:
    """A no-op live emitter — a batch run has no SSE observers (events are still persisted)."""


async def select_uninvestigated(
    session: AsyncSession, *, agency_id: uuid.UUID, limit: int = _DEFAULT_BATCH_LIMIT
) -> list[uuid.UUID]:
    """Return the ids of the agency's not-yet-investigated transactions (oldest first, capped)."""
    stmt = (
        select(Transaction.id)
        .where(Transaction.agency_id == agency_id, Transaction.latest_run_id.is_(None))
        .order_by(Transaction.ingested_at.asc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def run_batch_score(
    *,
    session: AsyncSession,
    components: PipelineComponents,
    settings: AppSettings,
    agency_id: uuid.UUID,
    transaction_ids: Sequence[uuid.UUID],
) -> BatchScoreResult:
    """Investigate each transaction sequentially via the pipeline + record a batch_score job."""
    txn_repo = TransactionRepository(session, agency_id)
    analysis_repo = AnalysisRunRepository(session, agency_id)
    completed = failed = skipped = 0
    for transaction_id in transaction_ids:
        transaction = await txn_repo.get(transaction_id)
        if transaction is None:
            skipped += 1
            continue
        run = await analysis_repo.create_running(transaction_id=transaction.id)
        await session.commit()
        pipeline_input = await build_pipeline_input(
            repo=txn_repo,
            transaction=transaction,
            run_id=run.id,
            agency_id=agency_id,
            settings=settings,
        )
        deps = await build_pipeline_deps(
            components=components,
            session=session,
            settings=settings,
            agency_id=agency_id,
            run_id=run.id,
            transaction_id=transaction.id,
            emit=_noop_emit,
        )
        report = await Runner(deps).run(pipeline_input)
        completed += report.status == "completed"
        failed += report.status == "failed"
    job = JobExecution(
        agency_id=agency_id,
        job_type=JobType.BATCH_SCORE,
        status=JobStatus.SUCCEEDED,
        payload={"requested": len(transaction_ids)},
        result={"completed": completed, "failed": failed, "skipped": skipped},
        attempts=1,
    )
    session.add(job)
    await session.commit()
    return BatchScoreResult(
        job_id=str(job.id),
        requested=len(transaction_ids),
        completed=completed,
        failed=failed,
        skipped=skipped,
    )


async def _amain() -> int:
    """Build the engine + components and batch-score the demo agency's un-investigated rows."""
    settings = get_settings()
    engine = create_engine_from_settings(settings)
    if engine is None:
        print("batch-score failed: DATABASE_URL is not configured")
        return 1
    components = build_pipeline_components(settings)
    try:
        async with build_sessionmaker(engine)() as session:
            transaction_ids = await select_uninvestigated(session, agency_id=DEMO_AGENCY_ID)
            result = await run_batch_score(
                session=session,
                components=components,
                settings=settings,
                agency_id=DEMO_AGENCY_ID,
                transaction_ids=transaction_ids,
            )
    finally:
        await engine.dispose()
    print(
        f"batch-score OK: {result.completed} completed, {result.failed} failed, "
        f"{result.skipped} skipped (of {result.requested} requested)"
    )
    return 0


def main() -> int:
    """CLI entry point: run the async batch-score and return its exit code."""
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())

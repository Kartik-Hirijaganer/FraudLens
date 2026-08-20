"""Summary: The batch investigation job runner (plan §16 Phase 8: "batch job runner"). It drives
the SAME LangGraph pipeline the interactive `POST /investigations` path uses, but headlessly — no
SSE stream, no `RunManager` background task — over a set of transactions, then records a PHI-free
`job_executions(batch_score)` row for the ops audit trail. Each transaction gets its own persisted
`analysis_runs` row + deterministic scoring core; only threshold-crossing runs continue through
RAG→SAR. The runner reuses the warm `PipelineComponents` (model cache + retriever + drafter) and
the per-run `RunStore`, so a batch
sweep produces exactly the same durable results/events/alerts an interactive run would. Runs are
sequential on one session (the store commits incrementally), so a mid-batch failure leaves prior
runs durable. `main` is the dev/demo entry point; it scores `--agency-id`, defaulting to the
CONFIGURED portfolio demo agency (`config/portfolio-demo.yaml`) rather than a source constant.
The Container Apps Jobs trigger is wired by Terraform; the local-vs-cloud seam is
`backends/jobs.py`.

Key classes:
- BatchScoreResult: the PHI-free outcome counts of a batch run (requested/completed/failed/skipped).

Key functions:
- run_batch_score: investigate a set of transactions sequentially + record a `job_executions` row.
- select_uninvestigated: the ids of an agency's not-yet-investigated transactions (capped).
- main: dev/demo entry point — build the engine + components and batch-score one tenant.

Notes:
- The live `EventEmitter` is a no-op here (batch has no observers); the pipeline still persists each
  ordered event, so a batch-scored run is fully replayable via `GET /investigations/{runId}/stream`.
- Per-transaction fault isolation: a transaction whose input cannot even be assembled (e.g. a
  legacy row violating a tightened contract) marks ITS run failed (`batch_input_error`) and the
  sweep continues — one poisoned row never aborts the batch.
- `main` is dev/demo-oriented (like `scripts/seed.py`) and scores un-investigated transactions, so
  re-running it is naturally incremental.
- `run_batch_score` stays generic and explicitly tenant-scoped; only the CLI default resolves the
  configured demo agency, and there is no "score every tenant" mode (one runtime agency exists).
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.db.models import JobExecution, JobStatus, JobType, Transaction
from fraudlens_backend.db.repositories import AnalysisRunRepository, TransactionRepository
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.pipeline_wiring import (
    PipelineComponents,
    build_pipeline_components,
    build_pipeline_deps,
    build_pipeline_input,
    resolve_workflow_mode,
)
from fraudlens_backend.portfolio_demo import load_portfolio_demo_config
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
    run_sessionmaker = async_sessionmaker(session.bind, expire_on_commit=False)
    for transaction_id in transaction_ids:
        transaction = await txn_repo.get(transaction_id)
        if transaction is None:
            skipped += 1
            continue
        workflow_mode = await resolve_workflow_mode(
            session,
            settings=settings,
            agency_id=agency_id,
        )
        run = await analysis_repo.create_running(
            transaction_id=transaction.id,
            workflow_mode=workflow_mode,
            graph_version=(
                components.agent_config.graph_version if workflow_mode == "multi_agent" else None
            ),
        )
        await session.commit()
        run_id, current_id = run.id, transaction.id  # plain values survive a rollback expiry
        try:
            pipeline_input = await build_pipeline_input(
                repo=txn_repo,
                transaction=transaction,
                run_id=run_id,
                agency_id=agency_id,
                settings=settings,
            )
            deps = await build_pipeline_deps(
                components=components,
                session=session,
                settings=settings,
                agency_id=agency_id,
                run_id=run_id,
                transaction_id=current_id,
                emit=_noop_emit,
                sessionmaker=run_sessionmaker,
                workflow_mode=workflow_mode,
            )
            report = await Runner(deps).run(pipeline_input)
        except Exception:  # one poisoned transaction must never abort the whole sweep
            await session.rollback()
            await analysis_repo.fail(run_id=run_id, error_code="batch_input_error")
            await session.commit()
            failed += 1
            continue
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


async def _amain(agency_id: uuid.UUID) -> int:
    """Build the engine + components and batch-score one tenant's un-investigated rows."""
    settings = get_settings()
    engine = create_engine_from_settings(settings)
    if engine is None:
        print("batch-score failed: DATABASE_URL is not configured")
        return 1
    components = build_pipeline_components(settings)
    try:
        async with build_sessionmaker(engine)() as session:
            transaction_ids = await select_uninvestigated(
                session, agency_id=agency_id, limit=settings.batch_score_limit
            )
            result = await run_batch_score(
                session=session,
                components=components,
                settings=settings,
                agency_id=agency_id,
                transaction_ids=transaction_ids,
            )
    finally:
        await engine.dispose()
    print(
        f"batch-score OK: {result.completed} completed, {result.failed} failed, "
        f"{result.skipped} skipped (of {result.requested} requested)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: resolve the target tenant, run the async batch-score, return its code."""
    parser = argparse.ArgumentParser(description="Batch-investigate a tenant's un-scored rows.")
    parser.add_argument(
        "--agency-id",
        default=None,
        help="Target agency id; defaults to the configured portfolio demo agency.",
    )
    args = parser.parse_args(argv)
    if args.agency_id is None:
        agency_id = load_portfolio_demo_config().agency.id
    else:
        try:
            agency_id = uuid.UUID(args.agency_id)
        except ValueError:
            print("batch-score failed: --agency-id must be a UUID")
            return 1
    return asyncio.run(_amain(agency_id))


if __name__ == "__main__":
    raise SystemExit(main())

"""Summary: Tenant-bound read models for saved investigations and CSV screening progress.

Key classes:
- (none)

Key functions:
- investigation_summaries: select one existing run per transaction without starting work.
- screening_progress: aggregate persisted upload screening counts.

Notes:
- Window functions keep list projections bounded and avoid one lookup per transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    AnalysisRun,
    JobExecution,
    JobType,
    RunStatus,
    SarDraft,
    SarStatus,
    Transaction,
)
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.models.transactions import InvestigationSummary, ScreeningProgress

_ACTIVE = (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.RETRYING)


async def investigation_summaries(
    session: AsyncSession,
    *,
    agency_id: uuid.UUID,
    transaction_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, InvestigationSummary]:
    """Prefer active work, then the saved pointer, then the most recent historical run."""
    if not transaction_ids:
        return {}
    ranked = (
        select(
            AnalysisRun.transaction_id,
            AnalysisRun.id,
            AnalysisRun.status,
            AnalysisRun.error_code,
            func.row_number()
            .over(
                partition_by=AnalysisRun.transaction_id,
                order_by=(
                    case(
                        (AnalysisRun.status.in_(_ACTIVE), 0),
                        (AnalysisRun.id == Transaction.latest_run_id, 1),
                        else_=2,
                    ),
                    AnalysisRun.created_at.desc(),
                    AnalysisRun.id.desc(),
                ),
            )
            .label("position"),
        )
        .join(Transaction, Transaction.id == AnalysisRun.transaction_id)
        .where(
            AnalysisRun.agency_id == agency_id,
            Transaction.agency_id == agency_id,
            AnalysisRun.transaction_id.in_(transaction_ids),
        )
        .subquery()
    )
    rows = (await session.execute(select(ranked).where(ranked.c.position == 1))).all()
    return {
        row.transaction_id: InvestigationSummary(
            run_id=str(row.id), status=row.status.value, error_code=row.error_code
        )
        for row in rows
    }


async def screening_progress(
    session: AsyncSession,
    *,
    agency_id: uuid.UUID,
    poll_after_ms: int,
    job_id: uuid.UUID | None = None,
) -> ScreeningProgress:
    """Read committed counts, with no scheduler or external-job side effects."""
    if job_id is not None:
        job = (
            await session.execute(
                select(JobExecution.id).where(
                    JobExecution.id == job_id,
                    JobExecution.agency_id == agency_id,
                    JobExecution.job_type == JobType.CSV_IMPORT,
                )
            )
        ).scalar_one_or_none()
        if job is None:
            raise AppError("screening_job_not_found")
    scope = [AnalysisRun.agency_id == agency_id, AnalysisRun.import_job_id.is_not(None)]
    if job_id is not None:
        scope.append(AnalysisRun.import_job_id == job_id)
    counts: dict[RunStatus, int] = {  # noqa: C416 - SQLAlchemy Row is not a typed tuple.
        status: count
        for status, count in (
            (
                await session.execute(
                    select(AnalysisRun.status, func.count())
                    .where(*scope)
                    .group_by(AnalysisRun.status)
                )
            ).all()
        )
    }
    drafts = (
        select(
            SarDraft.run_id,
            SarDraft.status,
            func.row_number()
            .over(
                partition_by=SarDraft.run_id,
                order_by=SarDraft.version.desc(),
            )
            .label("position"),
        )
        .where(SarDraft.agency_id == agency_id)
        .subquery()
    )
    blocked = (
        await session.execute(
            select(func.count())
            .select_from(AnalysisRun)
            .join(
                drafts,
                drafts.c.run_id == AnalysisRun.id,
            )
            .where(
                *scope,
                AnalysisRun.status == RunStatus.COMPLETED,
                drafts.c.position == 1,
                drafts.c.status == SarStatus.FAILED,
            )
        )
    ).scalar_one()
    return ScreeningProgress(
        total=sum(counts.values()),
        pending=counts.get(RunStatus.PENDING, 0),
        running=counts.get(RunStatus.RUNNING, 0),
        retrying=counts.get(RunStatus.RETRYING, 0),
        completed=counts.get(RunStatus.COMPLETED, 0),
        failed=counts.get(RunStatus.FAILED, 0),
        drafting_blocked=blocked,
        poll_after_ms=poll_after_ms,
    )

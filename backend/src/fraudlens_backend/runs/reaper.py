"""Summary: Transactional recovery pass for expired durable investigation leases.

Key classes:
- (none)

Key functions:
- reap_stale_runs: retry expired claims or fail them with a durable terminal SSE event.

Notes:
- The global scheduler may discover expired runs, but terminal event writes are rebound to each
  row's persisted agency_id through AnalysisRunRepository before mutation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import AnalysisRunEventType
from fraudlens_backend.db.repositories import AnalysisRunRepository
from fraudlens_backend.runs.leases import ReapResult, reap_expired_runs
from fraudlens_backend.settings import AppSettings


async def reap_stale_runs(
    session: AsyncSession,
    settings: AppSettings,
    *,
    now: datetime | None = None,
) -> ReapResult:
    """Apply bounded stale-lease recovery and append terminal events atomically."""
    result = await reap_expired_runs(
        session,
        now=now or datetime.now(UTC),
        max_attempts=settings.run_max_attempts,
        retry_backoff_seconds=settings.run_retry_backoff_seconds,
    )
    for failure in result.failed_runs:
        await AnalysisRunRepository(session, failure.agency_id).append_event(
            run_id=failure.run_id,
            event_type=AnalysisRunEventType.RUN_FAILED,
            payload={"code": failure.error_code},
        )
    return result

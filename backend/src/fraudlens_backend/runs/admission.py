"""Summary: Cross-replica admission control for paid multi-agent investigation runs.

Key classes:
- (none)

Key functions:
- reserve_agent_spend: serialize per-tenant admission and persist worst-case active-run spend.

Notes:
- Locking the tenant's Agency row makes the read-plus-reserve decision atomic on PostgreSQL.
- Terminal runs retain the reservation for audit but are excluded from active reservation totals.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import Agency, AnalysisRun, RunStatus
from fraudlens_backend.db.repositories import DashboardRepository, load_llm_daily_budget_usd
from fraudlens_backend.models.errors import AppError

_ACTIVE_RUN_STATUSES = (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.RETRYING)


async def reserve_agent_spend(
    session: AsyncSession,
    *,
    run: AnalysisRun,
    agency_id: uuid.UUID,
    maximum_attempt_cost_usd: Decimal,
) -> Decimal:
    """Reserve one run's worst-case attempts under a tenant-row transaction lock."""
    if run.agency_id != agency_id:
        raise AppError("llm_budget_exceeded")
    agency = (
        await session.execute(select(Agency.id).where(Agency.id == agency_id).with_for_update())
    ).scalar_one_or_none()
    if agency is None:
        raise AppError("llm_budget_exceeded")
    limit = await load_llm_daily_budget_usd(session, agency_id=agency_id)
    spent = await DashboardRepository(session, agency_id).sar_cost_today(as_of=datetime.now(UTC))
    reserved = (
        await session.execute(
            select(func.sum(AnalysisRun.llm_reserved_usd)).where(
                AnalysisRun.agency_id == agency_id,
                AnalysisRun.id != run.id,
                AnalysisRun.status.in_(_ACTIVE_RUN_STATUSES),
            )
        )
    ).scalar_one_or_none() or Decimal("0")
    if spent + reserved + maximum_attempt_cost_usd > limit:
        raise AppError("llm_budget_exceeded")
    run.llm_reserved_usd = maximum_attempt_cost_usd
    await session.flush()
    return maximum_attempt_cost_usd

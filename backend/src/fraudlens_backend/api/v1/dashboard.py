"""Summary: The dashboard-metrics API (plan §5.3 endpoint 13, §16 Phase 12). `GET
/api/v1/dashboard/metrics` returns the analyst landing page's aggregate — alert/transaction/run/SAR
counts, SAR LLM spend, and live model-health signals — in one tenant-scoped, PHI-free payload. The
route is scoped to the verified JWT `agency_id` (via `get_tenant`, never a path/body tenant), so the
counts reflect ONLY the caller's tenant (cross-tenant activity is invisible, plan §6.4); the global
model-health signals (active/canary pointer + latest advisory drift) are shared registry facts
(ADR-015). The aggregation itself lives in `DashboardRepository`; this module only maps its result
onto the camelCase response, filling any absent status with zero so every count field is present.

Key classes:
- (none)

Key functions:
- read_dashboard_metrics: GET /dashboard/metrics — the tenant-scoped dashboard aggregate.

Notes:
- `total` for each status group is the sum over all of that group's statuses (so it stays correct
  even if a status has zero rows and is absent from the grouped query result).
- Cost fields are stringified Decimals (matching the SAR view + `sar_drafts.cost_usd`), so the
  dashboard renders exact money without binary-float drift (plan §11.5).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from fraudlens_backend.api.deps import DbSessionDep, get_tenant
from fraudlens_backend.db.models.enums import AlertStatus, RunStatus, SarStatus
from fraudlens_backend.db.repositories import DashboardData, DashboardRepository
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.dashboard import (
    AlertMetrics,
    DashboardMetricsResponse,
    LlmCostMetrics,
    ModelHealthMetrics,
    RunMetrics,
    SarMetrics,
    TransactionMetrics,
)

router = APIRouter(tags=["dashboard"])

TenantDep = Annotated[TenantContext, Depends(get_tenant)]


def _to_response(data: DashboardData) -> DashboardMetricsResponse:
    """Map the aggregate onto the camelCase response, filling absent statuses with zero."""
    alerts = data.alert_counts
    runs = data.run_counts
    sar = data.sar_counts
    return DashboardMetricsResponse(
        alerts=AlertMetrics(
            open=alerts.get(AlertStatus.OPEN.value, 0),
            pending_review=alerts.get(AlertStatus.PENDING_REVIEW.value, 0),
            in_review=alerts.get(AlertStatus.IN_REVIEW.value, 0),
            escalated=alerts.get(AlertStatus.ESCALATED.value, 0),
            resolved=alerts.get(AlertStatus.RESOLVED.value, 0),
            dismissed=alerts.get(AlertStatus.DISMISSED.value, 0),
            total=sum(alerts.values()),
        ),
        transactions=TransactionMetrics(
            total=data.transaction_total, by_risk_band=data.transaction_risk_bands
        ),
        runs=RunMetrics(
            pending=runs.get(RunStatus.PENDING.value, 0),
            running=runs.get(RunStatus.RUNNING.value, 0),
            completed=runs.get(RunStatus.COMPLETED.value, 0),
            failed=runs.get(RunStatus.FAILED.value, 0),
            total=sum(runs.values()),
        ),
        sar=SarMetrics(
            draft=sar.get(SarStatus.DRAFT.value, 0),
            reviewed=sar.get(SarStatus.REVIEWED.value, 0),
            approved=sar.get(SarStatus.APPROVED.value, 0),
            rejected=sar.get(SarStatus.REJECTED.value, 0),
            failed=sar.get(SarStatus.FAILED.value, 0),
            total=sum(sar.values()),
        ),
        llm_cost=LlmCostMetrics(
            today_usd=str(data.sar_cost_today),
            total_usd=str(data.sar_cost_total),
            draft_count=data.sar_draft_count,
        ),
        model_health=ModelHealthMetrics(
            active_version_label=data.active_version_label,
            canary_version_label=data.canary_version_label,
            canary_percent=data.canary_percent,
            recent_inference_count=data.recent_inference_count,
            latest_drift_severity=data.latest_drift_severity,
        ),
    )


@router.get("/dashboard/metrics", response_model=DashboardMetricsResponse)
async def read_dashboard_metrics(
    tenant: TenantDep, session: DbSessionDep
) -> DashboardMetricsResponse:
    """Return the tenant-scoped dashboard aggregate (counts + LLM cost + live model health)."""
    data = await DashboardRepository(session, uuid.UUID(tenant.agency_id)).collect(
        as_of=datetime.now(UTC)
    )
    return _to_response(data)

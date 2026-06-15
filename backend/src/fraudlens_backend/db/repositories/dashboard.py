"""Summary: The dashboard-metrics aggregation repository (plan §5.3 endpoint 13, §16 Phase 12). It
computes the analyst dashboard's read-only aggregate in one place: the TENANT-scoped counts (alerts
/transactions/runs/SAR drafts by status, the risk-band distribution, and SAR LLM spend) are all
filtered by the bound `agency_id` so one tenant never sees another's activity (plan §6.4), while the
PLATFORM model-health signals (active/canary pointer + latest advisory drift) are read through the
existing `ModelRegistryRepository`/`ModelLifecycleRepository` (models are global, ADR-015) so this
repo never re-implements the registry resolution (no duplication, rule 5). Everything it returns is
PHI-free by construction — counts, version labels, and a NUMERIC cost — never an account, note, or
input value. It is a bespoke aggregator (not a `TenantScopedRepository`) because it spans tenant and
platform tables and returns counts rather than rows.

Key classes:
- DashboardData: the assembled, PHI-free dashboard aggregate (tenant counts + model health).
- DashboardRepository: computes the dashboard aggregate for one tenant.

Key functions:
- (none)

Notes:
- `sar_cost_today` sums `sar_drafts.cost_usd` since `as_of`'s UTC midnight; the caller passes the
  current time so "today" tracks the daily LLM budget window (plan §7.6) deterministically in tests.
- Status counts are returned as `{enumValue: count}` dicts; the API layer fills any absent status
  with zero so the contract is a complete, named field set (no missing keys).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from fraudlens_backend.db.models import (
    Alert,
    AnalysisRun,
    ModelInferenceLog,
    ModelVersion,
    SarDraft,
    Transaction,
)
from fraudlens_backend.db.repositories.model_lifecycle import ModelLifecycleRepository
from fraudlens_backend.db.repositories.model_registry import ModelRegistryRepository

_UNSCORED_RISK_BAND = "unscored"


def _to_decimal(value: object) -> Decimal:
    """Coerce a SUM() result (Decimal/float/None) to an exact Decimal (0 when no rows)."""
    if value is None:
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True)
class DashboardData:
    """The assembled, PHI-free dashboard aggregate for one tenant (+ global model health)."""

    alert_counts: dict[str, int]
    transaction_total: int
    transaction_risk_bands: dict[str, int]
    run_counts: dict[str, int]
    sar_counts: dict[str, int]
    sar_cost_today: Decimal
    sar_cost_total: Decimal
    sar_draft_count: int
    recent_inference_count: int
    active_version_label: str | None
    canary_version_label: str | None
    canary_percent: int
    latest_drift_severity: str | None


class DashboardRepository:
    """Computes the tenant-scoped dashboard aggregate (+ global model health)."""

    def __init__(self, session: AsyncSession, agency_id: uuid.UUID) -> None:
        """Bind the session and the tenant scope the aggregate counts are filtered by."""
        self._session = session
        self._agency_id = agency_id

    async def collect(self, *, as_of: datetime) -> DashboardData:
        """Compute the full dashboard aggregate (tenant counts + global model health)."""
        transaction_total, risk_bands = await self._transaction_metrics()
        cost_today, cost_total, draft_count = await self._sar_cost(as_of=as_of)
        active_label, canary_label, canary_percent = await self._deployment_health()
        return DashboardData(
            alert_counts=await self._status_counts(Alert.agency_id, Alert.status),
            transaction_total=transaction_total,
            transaction_risk_bands=risk_bands,
            run_counts=await self._status_counts(AnalysisRun.agency_id, AnalysisRun.status),
            sar_counts=await self._status_counts(SarDraft.agency_id, SarDraft.status),
            sar_cost_today=cost_today,
            sar_cost_total=cost_total,
            sar_draft_count=draft_count,
            recent_inference_count=await self._inference_count(),
            active_version_label=active_label,
            canary_version_label=canary_label,
            canary_percent=canary_percent,
            latest_drift_severity=await self._latest_drift_severity(),
        )

    async def _status_counts(
        self,
        agency_column: InstrumentedAttribute[uuid.UUID],
        status_column: InstrumentedAttribute[Any],
    ) -> dict[str, int]:
        """Return `{statusValue: count}` for a tenant-scoped table grouped by its status enum."""
        stmt = (
            select(status_column, func.count())
            .where(agency_column == self._agency_id)
            .group_by(status_column)
        )
        return {row[0].value: int(row[1]) for row in await self._session.execute(stmt)}

    async def _transaction_metrics(self) -> tuple[int, dict[str, int]]:
        """Return (total transactions, count per risk band) for the tenant (None → `unscored`)."""
        stmt = (
            select(Transaction.risk_band, func.count())
            .where(Transaction.agency_id == self._agency_id)
            .group_by(Transaction.risk_band)
        )
        bands: dict[str, int] = {}
        total = 0
        for band, count in await self._session.execute(stmt):
            key = band.value if band is not None else _UNSCORED_RISK_BAND
            bands[key] = int(count)
            total += int(count)
        return total, bands

    async def _sar_cost(self, *, as_of: datetime) -> tuple[Decimal, Decimal, int]:
        """Return (today's USD spend since UTC midnight, all-time USD spend, draft count)."""
        day_start = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
        scope = SarDraft.agency_id == self._agency_id
        total = _to_decimal(
            (await self._session.execute(select(func.sum(SarDraft.cost_usd)).where(scope))).scalar()
        )
        today = _to_decimal(
            (
                await self._session.execute(
                    select(func.sum(SarDraft.cost_usd)).where(
                        scope, SarDraft.created_at >= day_start
                    )
                )
            ).scalar()
        )
        count = int(
            (
                await self._session.execute(select(func.count()).select_from(SarDraft).where(scope))
            ).scalar_one()
        )
        return today, total, count

    async def _inference_count(self) -> int:
        """Return the tenant's hash-only inference-log count (model-activity proxy, plan §9.2)."""
        stmt = (
            select(func.count())
            .select_from(ModelInferenceLog)
            .where(ModelInferenceLog.agency_id == self._agency_id)
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def _deployment_health(self) -> tuple[str | None, str | None, int]:
        """Resolve the global active/canary pointer into (activeLabel, canaryLabel, percent)."""
        deployment = await ModelRegistryRepository(self._session).get_active_deployment()
        if deployment is None:
            return None, None, 0
        active = await self._session.get(ModelVersion, deployment.active_version_id)
        canary_label: str | None = None
        if deployment.canary_version_id is not None:
            canary = await self._session.get(ModelVersion, deployment.canary_version_id)
            canary_label = canary.version_label if canary is not None else None
        return (
            active.version_label if active is not None else None,
            canary_label,
            deployment.canary_percent,
        )

    async def _latest_drift_severity(self) -> str | None:
        """Return the most recent advisory drift report's severity, or None when there are none."""
        reports = await ModelLifecycleRepository(self._session).list_drift_reports(limit=1)
        return reports[0].severity.value if reports else None

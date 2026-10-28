"""Summary: Tenant-scoped case lookups, append-only history reads and terminal training labels.

Key classes:
- AlertRepository: tenant-scoped case persistence and query operations.
- AlertSummaryRow: internal joined alert and transaction projection.

Key functions:
- compute_review_flags: derive current mandatory-review reasons from persisted evidence.
- load_label_maturity_days: resolve the label maturity policy from global configuration.

Notes:
- Contextual mutation policy and row locking belong to the shared case workflow service.
- Review flags are recomputed after recovery instead of accumulating historical failures.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from fraudlens_backend.db.models import (
    Alert,
    AlertAction,
    AlertStatus,
    CaseContributor,
    CaseDecision,
    CaseSubmission,
    SarStatus,
    SystemConfig,
    TrainingLabel,
    TrainingLabelType,
    Transaction,
    User,
)
from fraudlens_backend.db.models.enums import LabelSource
from fraudlens_backend.db.repositories.base import TenantScopedRepository
from fraudlens_core import RiskBand

# The label maturity window (plan §10.4 "matured >= 30d (14d dev)"); a DB tunable in global
# `system_config` (`labelMaturityDays`), with this safe in-process default on any miss (§9.1).
_LABEL_MATURITY_KEY = "labelMaturityDays"
_DEFAULT_LABEL_MATURITY_DAYS = 30

# PHI-free force-review flag reasons (plan §8.5); each is {flag, reason} stored in review_flags.
_FLAG_CRITICAL = {
    "flag": "critical_risk_band",
    "reason": "Risk band is critical; mandatory human review.",
}
_FLAG_LOW_CONFIDENCE = {
    "flag": "low_model_confidence",
    "reason": "Model probability is near the decision boundary; confirm before acting.",
}
_FLAG_SAR_UNAVAILABLE = {
    "flag": "sar_unavailable",
    "reason": "The SAR could not be drafted automatically; manual authoring is required.",
}


@dataclass(frozen=True)
class AlertSummaryRow:
    """Joined alert row plus linked transaction money fields for API summary views."""

    alert: Alert
    amount: Decimal
    currency: str
    assigned_to_name: str | None


def compute_review_flags(
    *,
    risk_band: RiskBand,
    fraud_probability: float | None,
    sar_status: str | None,
    low_confidence_margin: float,
) -> list[dict[str, str]]:
    """Derive the PHI-free force-review flags for a raised alert (plan §8.5, Phase 9).

    Flags when the risk band is critical, the model probability sits within
    `low_confidence_margin` of the 0.5 boundary, or the SAR draft failed (needs manual authoring).
    """
    flags: list[dict[str, str]] = []
    if risk_band == RiskBand.CRITICAL:
        flags.append(dict(_FLAG_CRITICAL))
    if fraud_probability is not None and abs(fraud_probability - 0.5) <= low_confidence_margin:
        flags.append(dict(_FLAG_LOW_CONFIDENCE))
    if sar_status == SarStatus.FAILED.value:
        flags.append(dict(_FLAG_SAR_UNAVAILABLE))
    return flags


def _coerce_maturity_days(raw: object) -> int:
    """Coerce a `system_config` value to a day count; fall back to the default on any non-number."""
    if isinstance(raw, bool):  # bool is an int subclass — never a valid day count
        return _DEFAULT_LABEL_MATURITY_DAYS
    if isinstance(raw, int):
        return raw
    if isinstance(raw, (str, float)):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return _DEFAULT_LABEL_MATURITY_DAYS
    return _DEFAULT_LABEL_MATURITY_DAYS


async def load_label_maturity_days(session: AsyncSession) -> int:
    """Resolve the label maturity window (days) from global `system_config`, else the default."""
    try:
        stmt = select(SystemConfig).where(
            SystemConfig.agency_id.is_(None), SystemConfig.key == _LABEL_MATURITY_KEY
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
    except Exception:  # a DB hiccup must never break a resolution → safe in-process default
        return _DEFAULT_LABEL_MATURITY_DAYS
    return _DEFAULT_LABEL_MATURITY_DAYS if row is None else _coerce_maturity_days(row.value)


class AlertRepository(TenantScopedRepository[Alert]):
    """Agency-scoped persistence + lookup for alerts, their actions, and resolution labels."""

    def __init__(self, session: AsyncSession, agency_id: uuid.UUID) -> None:
        """Bind the session + agency scope to the `alerts` table."""
        super().__init__(session, Alert, agency_id)

    def _summary_stmt(self) -> Select[tuple[Alert, Decimal, str, str]]:
        """Build the shared tenant-scoped alert/transaction/assignee projection."""
        return (
            select(Alert, Transaction.amount, Transaction.currency, User.display_name)
            .join(Transaction, Alert.transaction_id == Transaction.id)
            .outerjoin(
                User,
                and_(Alert.assigned_to == User.id, User.agency_id == self._agency_id),
            )
            .where(Alert.agency_id == self._agency_id, Transaction.agency_id == self._agency_id)
        )

    def _queue_stmt(
        self,
        *,
        status: AlertStatus | None = None,
        queue: str | None = None,
        person_id: uuid.UUID | None = None,
    ) -> Select[tuple[Alert, Decimal, str, str]]:
        """Share tenant, status and person eligibility filters between pages and counts."""
        stmt = self._summary_stmt()
        if queue == "available":
            stmt = stmt.where(
                Alert.assigned_to.is_(None),
                Alert.status.in_([AlertStatus.OPEN, AlertStatus.PENDING_REVIEW]),
            )
        elif queue in {"mine", "returned"}:
            stmt = stmt.where(
                Alert.owner_person_id == person_id, Alert.status == AlertStatus.IN_REVIEW
            )
            if queue == "returned":
                returned = (
                    select(CaseSubmission.id)
                    .join(
                        CaseDecision,
                        (CaseDecision.submission_id == CaseSubmission.id)
                        & (CaseDecision.agency_id == self._agency_id),
                    )
                    .where(
                        CaseSubmission.agency_id == self._agency_id,
                        CaseSubmission.alert_id == Alert.id,
                        CaseDecision.decision == "return",
                    )
                    .exists()
                )
                stmt = stmt.where(returned)
        elif queue == "awaiting_review":
            contributed = (
                select(CaseContributor.id)
                .where(
                    CaseContributor.agency_id == self._agency_id,
                    CaseContributor.alert_id == Alert.id,
                    CaseContributor.person_id == person_id,
                )
                .exists()
            )
            active = (
                select(CaseSubmission.id)
                .outerjoin(
                    CaseDecision,
                    (CaseDecision.submission_id == CaseSubmission.id)
                    & (CaseDecision.agency_id == self._agency_id),
                )
                .where(
                    CaseSubmission.agency_id == self._agency_id,
                    CaseSubmission.alert_id == Alert.id,
                    CaseDecision.id.is_(None),
                )
                .exists()
            )
            stmt = stmt.where(Alert.status == AlertStatus.ESCALATED, ~contributed, active)
        elif queue == "closed":
            stmt = stmt.where(Alert.status.in_([AlertStatus.RESOLVED, AlertStatus.DISMISSED]))
        if status is not None:
            stmt = stmt.where(Alert.status == status)
        return stmt

    async def count_alerts(
        self,
        *,
        status: AlertStatus | None = None,
        queue: str | None = None,
        person_id: uuid.UUID | None = None,
    ) -> int:
        """Count every eligible case before pagination, using the exact list filters."""
        filtered = self._queue_stmt(status=status, queue=queue, person_id=person_id)
        stmt = select(func.count()).select_from(filtered.subquery())
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_alerts(
        self,
        *,
        limit: int | None = 50,
        offset: int = 0,
        status: AlertStatus | None = None,
        queue: str | None = None,
        person_id: uuid.UUID | None = None,
    ) -> Sequence[AlertSummaryRow]:
        """Return eligible agency alert summaries (newest first), after filtering."""
        stmt = (
            self._queue_stmt(status=status, queue=queue, person_id=person_id)
            .order_by(Alert.created_at.desc(), Alert.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [
            AlertSummaryRow(
                alert=alert,
                amount=amount,
                currency=currency,
                assigned_to_name=assigned_to_name,
            )
            for alert, amount, currency, assigned_to_name in await self._session.execute(stmt)
        ]

    async def get_alert_summary(self, alert_id: uuid.UUID) -> AlertSummaryRow | None:
        """Return one joined alert summary row, or None when missing/cross-tenant."""
        stmt = self._summary_stmt().where(Alert.id == alert_id)
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None
        alert, amount, currency, assigned_to_name = row
        return AlertSummaryRow(
            alert=alert,
            amount=amount,
            currency=currency,
            assigned_to_name=assigned_to_name,
        )

    async def get_for_run(self, run_id: uuid.UUID) -> Alert | None:
        """Return this agency's alert for an investigation run, or None when none was raised."""
        stmt = (
            select(Alert)
            .where(
                Alert.agency_id == self._agency_id,
                Alert.run_id == run_id,
            )
            .order_by(Alert.created_at.desc(), Alert.id.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_actions(self, alert_id: uuid.UUID) -> Sequence[AlertAction]:
        """Return the alert's append-only action trail, newest first (agency-scoped)."""
        stmt = (
            select(AlertAction)
            .where(AlertAction.agency_id == self._agency_id, AlertAction.alert_id == alert_id)
            .order_by(AlertAction.created_at.desc(), AlertAction.id.desc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def create_training_label(  # noqa: PLR0913 - label provenance fields stay explicit.
        self,
        *,
        transaction_id: uuid.UUID,
        run_id: uuid.UUID,
        label: TrainingLabelType,
        created_by: uuid.UUID,
        matured_at: datetime,
        source: LabelSource = LabelSource.ANALYST_REVIEW,
    ) -> TrainingLabel:
        """Write the `training_labels` row a reviewed/dismissed decision produces."""
        row = TrainingLabel(
            agency_id=self._agency_id,
            transaction_id=transaction_id,
            run_id=run_id,
            label=label,
            source=source,
            matured_at=matured_at,
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        return row

"""Summary: The agency-scoped alert/review-workflow repository + its centralized domain helpers
(plan §5.4, §8.5, §10.4, §16 Phase 9). Built on `TenantScopedRepository`, so every read/write is
bound to one `agency_id` and a cross-tenant alert id resolves to nothing (no existence leak, plan
§6.4). It is the single seam the alerts API triages through: it lists/filters the agency's alerts,
appends the **append-only** `alert_actions` audit trail, applies the validated status transition,
validates an assignee belongs to the agency (cross-tenant assignee → 403), and writes the
`training_labels` row a resolution produces (plan §10.4). The pure module-level helpers
`next_alert_status` (the centralized state machine) and `compute_review_flags` (the force-review
reasons stamped at investigation time, §8.5) carry no IO so they are unit-testable in isolation and
reused by the pipeline alert-raise seam.

Key classes:
- AlertRepository: agency-scoped persistence + lookup for alerts, their actions, and labels.

Key functions:
- next_alert_status: the centralized legal-transition function (None when the action is illegal).
- compute_review_flags: derive the PHI-free force-review reasons from a run's risk signals.
- load_label_maturity_days: resolve the label maturity window from global `system_config`.

Notes:
- `alert_actions` is append-only: a transition records a `from_status`→`to_status` row rather than
  mutating history; `note` is stored PHI-masked by the caller (the value never reaches a log/audit).
- Terminal alert statuses (`resolved`/`dismissed`) admit no further actions, so the state machine
  returns None and the API surfaces `invalid_alert_transition` (409).
- `compute_review_flags` is total over partial runs: a missing probability or SAR simply omits the
  corresponding flag, so a degraded run still raises a well-formed (possibly empty) flag set.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    Alert,
    AlertAction,
    AlertActionType,
    AlertStatus,
    SarStatus,
    SystemConfig,
    TrainingLabel,
    TrainingLabelType,
    User,
)
from fraudlens_backend.db.repositories.base import TenantScopedRepository
from fraudlens_core import RiskBand

# The label maturity window (plan §10.4 "matured >= 30d (14d dev)"); a DB tunable in global
# `system_config` (`labelMaturityDays`), with this safe in-process default on any miss (§9.1).
_LABEL_MATURITY_KEY = "labelMaturityDays"
_DEFAULT_LABEL_MATURITY_DAYS = 30

# Statuses from which no further triage action is legal (the alert is closed).
_TERMINAL_ALERT_STATUSES: frozenset[AlertStatus] = frozenset(
    {AlertStatus.RESOLVED, AlertStatus.DISMISSED}
)

# The centralized alert state machine: action -> resulting status from a non-terminal alert.
# `comment` is intentionally absent — it never changes status (it records an audit-trail note).
_ACTION_TARGET: dict[AlertActionType, AlertStatus] = {
    AlertActionType.ASSIGN: AlertStatus.IN_REVIEW,
    AlertActionType.ESCALATE: AlertStatus.IN_REVIEW,
    AlertActionType.RESOLVE: AlertStatus.RESOLVED,
    AlertActionType.DISMISS: AlertStatus.DISMISSED,
}

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


def next_alert_status(current: AlertStatus, action: AlertActionType) -> AlertStatus | None:
    """Return the alert status after `action`, or None when the action is illegal (plan §5.4).

    `comment` leaves the status unchanged; the other actions move an open/in-review alert per the
    centralized state machine. A terminal (resolved/dismissed) alert admits no action.
    """
    if current in _TERMINAL_ALERT_STATUSES:
        return None
    if action == AlertActionType.COMMENT:
        return current
    return _ACTION_TARGET.get(action)


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

    async def list_alerts(
        self, *, limit: int = 50, offset: int = 0, status: AlertStatus | None = None
    ) -> Sequence[Alert]:
        """Return the agency's alerts (newest first), optionally filtered by status."""
        stmt = select(Alert).where(Alert.agency_id == self._agency_id)
        if status is not None:
            stmt = stmt.where(Alert.status == status)
        stmt = stmt.order_by(Alert.created_at.desc(), Alert.id.desc()).limit(limit).offset(offset)
        return (await self._session.execute(stmt)).scalars().all()

    async def list_actions(self, alert_id: uuid.UUID) -> Sequence[AlertAction]:
        """Return the alert's append-only action trail, newest first (agency-scoped)."""
        stmt = (
            select(AlertAction)
            .where(AlertAction.agency_id == self._agency_id, AlertAction.alert_id == alert_id)
            .order_by(AlertAction.created_at.desc(), AlertAction.id.desc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def user_in_agency(self, user_id: uuid.UUID) -> bool:
        """True when a user with that id belongs to this agency (assignee tenant check)."""
        stmt = select(User.id).where(User.id == user_id, User.agency_id == self._agency_id)
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def record_action(  # noqa: PLR0913 - the action + its status/assignment (keyword-only).
        self,
        *,
        alert: Alert,
        actor_id: uuid.UUID,
        action: AlertActionType,
        to_status: AlertStatus,
        note: str | None = None,
        assigned_to: uuid.UUID | None = None,
    ) -> AlertAction:
        """Append the action row and apply the alert's resulting status/assignment (one unit).

        `note` is supplied already PHI-masked by the caller; `to_status` is the validated target
        from `next_alert_status`. An `assign` carries the (already agency-validated) assignee.
        """
        entry = AlertAction(
            agency_id=self._agency_id,
            alert_id=alert.id,
            actor_id=actor_id,
            action=action,
            note=note,
            from_status=alert.status.value,
            to_status=to_status.value,
        )
        self._session.add(entry)
        alert.status = to_status
        if action == AlertActionType.ASSIGN:
            alert.assigned_to = assigned_to
        await self._session.flush()
        return entry

    async def create_training_label(
        self,
        *,
        transaction_id: uuid.UUID,
        run_id: uuid.UUID,
        label: TrainingLabelType,
        created_by: uuid.UUID,
        matured_at: datetime,
    ) -> TrainingLabel:
        """Write the `training_labels` row a resolution produces (analyst-review source, §10.4)."""
        row = TrainingLabel(
            agency_id=self._agency_id,
            transaction_id=transaction_id,
            run_id=run_id,
            label=label,
            matured_at=matured_at,
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        return row

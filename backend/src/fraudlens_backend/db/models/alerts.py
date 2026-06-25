"""Summary: The alert/review tables (plan §9.1): `alerts` (raised when a run crosses the
threshold), the append-only `alert_actions` audit trail of triage decisions, and
`sar_drafts` (the LLM-drafted Suspicious Activity Report, always human-reviewed). All are
tenant-scoped (NOT NULL `agency_id`, indexed). Free-text fields that could carry PHI
(`alert_actions.note`, `sar_drafts.content`) are stored **masked** — masking lands in the
PHI/SAR phases; the schema here only guarantees the columns exist and are tenant-scoped.

Key classes:
- Alert: a tenant-scoped alert raised from an analysis run.
- AlertAction: one append-only triage action recorded against an alert.
- SarDraft: a draft Suspicious Activity Report (masked content + citations + status).

Key functions:
- (none)

Notes:
- `alert_actions` is append-only (CreatedAtMixin, no `updated_at`); status transitions are
  recorded as `from_status` → `to_status` rather than mutating prior rows.
- `sar_drafts.citations` are grounded regulatory references; `structured` holds the typed
  SAR body. `cost_usd` / `token_usage` capture LLM spend for the audit trail (plan §7.4).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from fraudlens_backend.db.base import (
    JSONB_TYPE,
    AgencyScopedMixin,
    Base,
    CreatedAtMixin,
    JsonValue,
    TimestampMixin,
    str_enum,
)
from fraudlens_backend.db.models.enums import AlertActionType, AlertStatus, SarStatus, Severity


class Alert(AgencyScopedMixin, TimestampMixin, Base):
    """A tenant-scoped alert raised from an analysis run that crossed the threshold."""

    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_agency_id_status", "agency_id", "status"),
        Index("ix_alerts_agency_id_assigned_to", "agency_id", "assigned_to"),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("transactions.id"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("analysis_runs.id"), nullable=False)
    status: Mapped[AlertStatus] = mapped_column(
        str_enum(AlertStatus, create_constraint=True), nullable=False, default=AlertStatus.OPEN
    )
    severity: Mapped[Severity] = mapped_column(str_enum(Severity), nullable=False)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    review_flags: Mapped[list[JsonValue]] = mapped_column(JSONB_TYPE, nullable=False, default=list)


class AlertAction(AgencyScopedMixin, CreatedAtMixin, Base):
    """One append-only triage action recorded against an alert (plan §5.4)."""

    __tablename__ = "alert_actions"
    __table_args__ = (Index("ix_alert_actions_agency_id_alert_id", "agency_id", "alert_id"),)

    alert_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("alerts.id"), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    action: Mapped[AlertActionType] = mapped_column(str_enum(AlertActionType), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)


class SarDraft(AgencyScopedMixin, TimestampMixin, Base):
    """A draft Suspicious Activity Report — masked content, citations, review status."""

    __tablename__ = "sar_drafts"
    __table_args__ = (Index("ix_sar_drafts_agency_id_run_id", "agency_id", "run_id"),)

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("analysis_runs.id"), nullable=False)
    alert_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("alerts.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    structured: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    citations: Mapped[list[JsonValue]] = mapped_column(JSONB_TYPE, nullable=False, default=list)
    status: Mapped[SarStatus] = mapped_column(
        str_enum(SarStatus), nullable=False, default=SarStatus.DRAFT
    )
    token_usage: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    pdf_blob_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )

"""Summary: Core tenant tables for FraudLens (plan §9.1): the platform `agencies`
table (the tenant root — no `agency_id`, CI-allowlisted) plus the tenant-scoped
`users`, `transactions`, and `aml_rules`. Tenant-scoped tables inherit
`AgencyScopedMixin` (NOT NULL `agency_id`) and index it via a composite index leading
with `agency_id`, satisfying the multi-tenant isolation invariant (scripts/check_tenancy.py).
`transactions` stores account identifiers **masked** with a `feature_hash` — raw PHI is
never persisted (ADR-014); the masking itself lands in Phase 3. `aml_rules.agency_id` is
nullable so a row can be a global default rule or a per-agency override.

Key classes:
- Agency: platform tenant record (id/name/slug); the root of tenant isolation.
- User: an auditor/analyst/reviewer/admin scoped to one agency.
- Transaction: a tenant-scoped financial transaction (masked identifiers + features).
- AmlRule: a deterministic AML rule definition (global when agency_id is NULL).

Key functions:
- (none)

Notes:
- `transactions.latest_run_id` is a denormalized pointer (nullable UUID, no FK) to avoid a
  circular dependency with `analysis_runs.transaction_id`; it is set after a run completes.
- `risk_band` reuses the canonical `fraudlens_core.RiskBand` (no duplicate enum).
- UNIQUE `(agency_id, external_id)` enforces per-tenant ingest idempotency (plan §5.2).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from fraudlens_backend.db.base import (
    JSONB_TYPE,
    AgencyScopedMixin,
    Base,
    CreatedAtMixin,
    IdMixin,
    JsonValue,
    TimestampMixin,
    str_enum,
)
from fraudlens_backend.db.models.enums import Severity, UserRole
from fraudlens_core import AmlRuleType, RiskBand


class Agency(IdMixin, Base):
    """Platform tenant record — the root of tenant isolation (no `agency_id`)."""

    __tablename__ = "agencies"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(AgencyScopedMixin, TimestampMixin, Base):
    """An auditor/analyst/reviewer/admin scoped to exactly one agency (plan §9.1)."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_agency_id_email", "agency_id", "email"),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(str_enum(UserRole), nullable=False)


class Transaction(AgencyScopedMixin, CreatedAtMixin, Base):
    """A tenant-scoped financial transaction; account identifiers are stored masked."""

    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("agency_id", "external_id", name="uq_transactions_agency_id_external_id"),
        Index("ix_transactions_agency_id_occurred_at", "agency_id", "occurred_at"),
        Index("ix_transactions_agency_id_risk_band", "agency_id", "risk_band"),
    )

    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    origin_account: Mapped[str] = mapped_column(String(128), nullable=False)
    dest_account: Mapped[str] = mapped_column(String(128), nullable=False)
    channel: Mapped[str] = mapped_column(String(128), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    features: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    feature_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_band: Mapped[RiskBand | None] = mapped_column(str_enum(RiskBand), nullable=True)
    latest_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AmlRule(IdMixin, TimestampMixin, Base):
    """A deterministic AML rule definition; global when `agency_id` is NULL (plan §9.1)."""

    __tablename__ = "aml_rules"
    __table_args__ = (Index("ix_aml_rules_agency_id_code", "agency_id", "code"),)

    # Nullable by design: NULL = a global (cross-tenant) default rule; a value = an override
    # for that agency. Such global-or-tenant tables do not use AgencyScopedMixin (NOT NULL).
    agency_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agencies.id"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rule_type: Mapped[AmlRuleType] = mapped_column(str_enum(AmlRuleType), nullable=False)
    params: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    severity: Mapped[Severity] = mapped_column(str_enum(Severity), nullable=False)
    weight: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False, default=Decimal("1.0"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

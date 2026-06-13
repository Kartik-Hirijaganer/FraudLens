"""Summary: Operational tables with a **nullable** `agency_id` (plan §9.1): `system_config`
(runtime/tenant tunables — NULL agency = a global default; boot-critical edge config lives
in YAML/env, not here, per §12.3), `job_executions` (background-job audit incl. the seed
run), and the append-only, PHI-free `audit_logs`. These are "global-or-tenant" tables, so
they use `IdMixin` and declare their own nullable `agency_id` rather than the NOT NULL
`AgencyScopedMixin`; `agency_id` is still indexed so the tenancy invariant holds.

Key classes:
- SystemConfig: a tunable config key/value (global when agency_id is NULL).
- JobExecution: a background-job execution record (type/status/result/attempts).
- AuditLog: an append-only, PHI-free audit row (actor/action/resource/requestId).

Key functions:
- (none)

Notes:
- `AuditLog.meta` maps to the DB column `metadata` (the attribute name `metadata` is
  reserved by SQLAlchemy's declarative base). Values are scrubbed — never raw PHI (§8.3).
- `system_config` has UNIQUE `(agency_id, key)`; that constraint's leading `agency_id`
  satisfies the indexed-tenant-column invariant without a redundant single-column index.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from fraudlens_backend.db.base import (
    JSONB_TYPE,
    Base,
    CreatedAtMixin,
    IdMixin,
    JsonValue,
    TimestampMixin,
    UpdatedAtMixin,
    str_enum,
)
from fraudlens_backend.db.models.enums import JobStatus, JobType


class SystemConfig(IdMixin, UpdatedAtMixin, Base):
    """A tunable config key/value; global when `agency_id` is NULL (plan §9.1 / §12.3)."""

    __tablename__ = "system_config"
    __table_args__ = (UniqueConstraint("agency_id", "key", name="uq_system_config_agency_id"),)

    agency_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agencies.id"), nullable=True
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )


class JobExecution(IdMixin, TimestampMixin, Base):
    """A background-job execution record (incl. the idempotent seed run, plan §9.1)."""

    __tablename__ = "job_executions"
    __table_args__ = (Index("ix_job_executions_agency_id", "agency_id"),)

    agency_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agencies.id"), nullable=True
    )
    job_type: Mapped[JobType] = mapped_column(str_enum(JobType), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus), nullable=False, default=JobStatus.PENDING
    )
    payload: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    result: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AuditLog(IdMixin, CreatedAtMixin, Base):
    """An append-only, PHI-free audit row (actor/action/resource/requestId, plan §9.1)."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_agency_id_created_at", "agency_id", "created_at"),
        Index("ix_audit_logs_resource_type_resource_id", "resource_type", "resource_id"),
    )

    agency_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agencies.id"), nullable=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Column is named `metadata` per plan §9.1; the attribute is `meta` because SQLAlchemy
    # reserves `metadata` on the declarative base. Values are scrubbed — never raw PHI.
    meta: Mapped[JsonValue] = mapped_column("metadata", JSONB_TYPE, nullable=False, default=dict)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)

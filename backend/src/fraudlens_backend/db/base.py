"""Summary: The SQLAlchemy 2.0 declarative base, shared column types, and reusable
mixins for every FraudLens ORM model (plan §9). `Base` carries a deterministic naming
convention so constraint/index names are stable across Alembic migrations and dialects
(Postgres in prod/local-demo, SQLite in tests). `JSONB_TYPE` is a portable JSON column —
`JSONB` on Postgres, generic `JSON` elsewhere — so the same models and the initial
migration run on both engines. The mixins encode the §9 invariants once: a UUID v4 PK
(`IdMixin`), the tenant-scoping `agency_id` FK that every strictly tenant-scoped table
carries (`AgencyScopedMixin` — NOT NULL; the tenancy invariant in scripts/check_tenancy.py
requires it indexed), and the audit timestamp columns (`CreatedAtMixin`, `TimestampMixin`).

Key classes:
- Base: declarative base shared by all ORM models (naming-convention metadata).
- IdMixin: adds the UUID v4 primary key generated application-side (portable).
- AgencyScopedMixin: adds the NOT NULL `agency_id` FK for tenant-scoped tables.
- CreatedAtMixin: adds a server-defaulted `created_at` (append-only tables).
- UpdatedAtMixin: adds a server-defaulted, auto-updated `updated_at` (update-only tables).
- TimestampMixin: composes `created_at` + `updated_at` (mutable tables).

Key functions:
- str_enum: build a portable enum column type that stores the member VALUE, not its name.

Notes:
- Enums are stored as strings (`native_enum=False`) so migrations stay portable and
  adding a value never needs a Postgres `ALTER TYPE` (expand/contract friendly, §9.3).
  `str_enum` sets `values_callable` so the persisted string is the enum's `.value`
  (e.g. "active", "run.started") — what the API surface, filters, and SSE matching expect,
  NOT the uppercase member name SQLAlchemy would store by default.
- `agency_id` is declared without a per-column index here; each tenant table indexes it
  via a composite index leading with `agency_id` (or an explicit single-column index) so
  there are no redundant indexes — the tenancy invariant only requires it be indexed.
- UUIDs default to `uuid4()` in Python (not a DB server default) so PK generation works
  identically on Postgres and SQLite.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, MetaData, Uuid, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic names for indexes/uniques/checks/FKs/PKs so Alembic diffs and cross-dialect
# DDL are stable (SQLAlchemy applies this to every constraint that is not explicitly named).
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Portable JSON column: JSONB on Postgres (indexable, binary), generic JSON elsewhere.
JSONB_TYPE = JSON().with_variant(JSONB(), "postgresql")


def str_enum(enum_cls: type[enum.Enum]) -> SAEnum:
    """Return a portable enum column type that stores the member VALUE (not its name)."""
    return SAEnum(
        enum_cls,
        native_enum=False,
        values_callable=lambda members: [str(member.value) for member in members],
    )


class Base(DeclarativeBase):
    """Declarative base for all FraudLens ORM models (naming-convention metadata)."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IdMixin:
    """Adds a UUID v4 primary key generated application-side (portable across engines)."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class AgencyScopedMixin(IdMixin):
    """Adds the NOT NULL `agency_id` tenant FK carried by every tenant-scoped table."""

    agency_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("agencies.id"), nullable=False)


class CreatedAtMixin:
    """Adds a server-defaulted `created_at` for append-only / immutable tables."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UpdatedAtMixin:
    """Adds a server-defaulted, auto-updated `updated_at` (for update-only tables)."""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TimestampMixin(CreatedAtMixin, UpdatedAtMixin):
    """Composes `created_at` + auto-updated `updated_at` for mutable tables."""


# Re-exported so model modules can annotate JSON columns without importing `typing.Any`.
JsonValue = dict[str, Any]

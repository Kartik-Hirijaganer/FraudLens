"""Summary: The SQLAlchemy 2.0 declarative base for every FraudLens ORM model.
Tables are NOT defined here in Phase 1 (the foundation provides the engine/session
seam only); the §9 schema — with `agency_id` on every tenant-scoped table and the
platform-table allowlist — lands in Phase 2 as models that subclass Base, plus the
initial Alembic migration. Keeping Base in its own module avoids import cycles between
the models and the session/engine factories.

Key classes:
- Base: declarative base class shared by all ORM models (Phase 2+).

Key functions:
- (none)

Notes:
- Intentionally minimal: metadata/naming conventions and the tenancy invariant are
  introduced alongside the first models in Phase 2 (plan §9.3).
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all FraudLens ORM models (tables land in Phase 2)."""

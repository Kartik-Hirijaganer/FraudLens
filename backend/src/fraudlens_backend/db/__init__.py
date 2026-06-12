"""Async database layer: declarative Base + engine/session factories + readiness ping.

Phase 1 provides the connectivity seam only (no tables); SQLAlchemy models, repositories,
and Alembic migrations land in Phase 2. Re-exports are intentional (see members).
"""

from __future__ import annotations

from fraudlens_backend.db.base import Base
from fraudlens_backend.db.session import (
    build_sessionmaker,
    create_engine_from_settings,
    dispose_engine,
    ping_database,
)

__all__ = [
    "Base",
    "build_sessionmaker",
    "create_engine_from_settings",
    "dispose_engine",
    "ping_database",
]

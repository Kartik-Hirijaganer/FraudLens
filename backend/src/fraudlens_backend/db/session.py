"""Summary: Async SQLAlchemy engine + session factory and a readiness ping for the
FraudLens backend. The engine is built from settings.database_url (a postgresql+asyncpg
URL injected from the environment — Infisical in prod, a local docker URL in dev); when
no URL is configured the factory returns None so the app still boots (the gateway/edge
posture is DB-independent — plan §12.3) and /readyz reports the database as "skipped".
ping_database runs a bounded `SELECT 1` so /readyz can gate traffic off a replica whose
database is unreachable. No ORM tables are defined in Phase 1 — only this connectivity
seam; repositories and migrations arrive in Phase 2.

Key classes:
- (none)

Key functions:
- create_engine_from_settings: build the async engine, or None when no URL is set.
- build_sessionmaker: an async_sessionmaker bound to a given engine.
- ping_database: run a timeout-bounded connectivity check (raises on failure).
- dispose_engine: dispose the engine's connection pool on shutdown.

Notes:
- ping_database surfaces NO connection-string detail to callers; /readyz maps a raised
  error to a PHI-free "down" so no credentials/host ever reach logs or responses.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fraudlens_backend.settings import AppSettings


def create_engine_from_settings(settings: AppSettings) -> AsyncEngine | None:
    """Return an async engine built from settings.database_url, or None when unset."""
    if not settings.database_url:
        return None
    return create_async_engine(settings.database_url, pool_pre_ping=True, future=True)


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return an async_sessionmaker bound to the engine (sessions don't auto-expire)."""
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def ping_database(engine: AsyncEngine, *, timeout_seconds: float) -> None:
    """Run a timeout-bounded `SELECT 1`; raise if the database is unreachable."""
    async with asyncio.timeout(timeout_seconds), engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def dispose_engine(engine: AsyncEngine) -> None:
    """Dispose the engine's connection pool (called on application shutdown)."""
    await engine.dispose()

"""Shared pytest fixtures: settings + TestClient factories and async DB fixtures.

The DB fixtures back the Phase 2 suite: an in-memory async SQLite engine (one shared
connection via StaticPool) whose schema is created from the ORM metadata, a sessionmaker
over it, and a request-scoped session. Production/local-demo run the same models on Postgres.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from fraudlens_backend.db.models import Base
from fraudlens_backend.main import create_app
from fraudlens_backend.settings import AppSettings

# Put scripts/ on the path so tests can spec-load the maintenance scripts
# (changed_files, next_version) and let them import their `lib.*` helpers.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Test templates are examples to copy, not live tests.
collect_ignore_glob = ["**/_template_test.py"]


@pytest.fixture
def make_settings() -> Callable[..., AppSettings]:
    """Return a factory building AppSettings with explicit, deterministic overrides."""

    def _make(**overrides: Any) -> AppSettings:
        params: dict[str, Any] = {
            "environment": "dev",
            "auth_dev_bypass": False,
            "log_level": "INFO",
        }
        params.update(overrides)
        return AppSettings(**params)

    return _make


@pytest.fixture
def client_factory(
    make_settings: Callable[..., AppSettings],
) -> Callable[..., TestClient]:
    """Return a factory building a TestClient over an app with the given settings."""

    def _make(**overrides: Any) -> TestClient:
        app = create_app(make_settings(**overrides))
        return TestClient(app, raise_server_exceptions=False)

    return _make


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """Yield an in-memory async SQLite engine with the full schema created (one connection)."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def db_sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return an async sessionmaker bound to the in-memory test engine."""
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def db_session(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped AsyncSession over the in-memory test engine."""
    async with db_sessionmaker() as session:
        yield session

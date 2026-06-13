"""Shared pytest fixtures: settings + TestClient factories and async DB fixtures.

The DB fixtures back the Phase 2 suite: an in-memory async SQLite engine (one shared
connection via StaticPool) whose schema is created from the ORM metadata, a sessionmaker
over it, and a request-scoped session. Production/local-demo run the same models on Postgres.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from decimal import Decimal
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
from fraudlens_core import RuleContext
from fraudlens_core.rules.base import RuleTransaction, TransactionDirection

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Put scripts/ on the path so tests can spec-load the maintenance scripts
# (changed_files, next_version) and let them import their `lib.*` helpers.
_SCRIPTS_DIR = str(_REPO_ROOT / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# The committed Phase 5 fixture model bundle the scorer/explainer tests load.
FIXTURE_MODEL_DIR = _REPO_ROOT / "data" / "models" / "v0-fixture"

# Test templates are examples to copy, not live tests.
collect_ignore_glob = ["**/_template_test.py"]


@pytest.fixture
def fixture_model_dir() -> Path:
    """Return the committed Phase 5 fixture model bundle directory (data/models/v0-fixture)."""
    return FIXTURE_MODEL_DIR


@pytest.fixture
def make_rule_context() -> Callable[..., RuleContext]:
    """Return a factory building a PHI-free RuleContext for scoring/feature tests."""

    def _make(
        *,
        amount: str = "100.00",
        country: str = "US",
        channel: str = "card",
        occurred_at: datetime | None = None,
        direction: TransactionDirection = TransactionDirection.OUTBOUND,
        history: tuple[RuleTransaction, ...] = (),
    ) -> RuleContext:
        txn = RuleTransaction(
            amount=Decimal(amount),
            currency="USD",
            country=country,
            channel=channel,
            occurred_at=occurred_at or datetime(2024, 6, 1, 14, 0, tzinfo=UTC),
            direction=direction,
        )
        return RuleContext(transaction=txn, history=history)

    return _make


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

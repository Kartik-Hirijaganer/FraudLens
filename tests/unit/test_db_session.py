"""Unit tests for the async engine factory, sessionmaker, and the readiness ping."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from fraudlens_backend.db.session import (
    build_sessionmaker,
    create_engine_from_settings,
    dispose_engine,
    ping_database,
)
from fraudlens_backend.settings import AppSettings

_URL = "postgresql+asyncpg://user:pass@localhost:5432/fraudlens"


class _FakeConn:
    """Async-context-manager connection that records a successful execute."""

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def execute(self, _statement: object) -> None:
        return None


class _OkEngine:
    """Engine stub whose connect() yields a working connection."""

    def connect(self) -> _FakeConn:
        return _FakeConn()


class _BadEngine:
    """Engine stub whose connect() fails (simulates an unreachable database)."""

    def connect(self) -> _FakeConn:
        raise OSError("connection refused")


def test_create_engine_returns_none_without_url() -> None:
    assert create_engine_from_settings(AppSettings(environment="dev")) is None


def test_create_engine_builds_from_database_url() -> None:
    engine = create_engine_from_settings(AppSettings(environment="dev", database_url=_URL))
    assert isinstance(engine, AsyncEngine)
    assert engine.url.host == "localhost"
    assert engine.url.database == "fraudlens"


def test_build_sessionmaker_is_bound_to_engine() -> None:
    engine = create_engine_from_settings(AppSettings(environment="dev", database_url=_URL))
    assert engine is not None
    maker = build_sessionmaker(engine)
    assert isinstance(maker, async_sessionmaker)


async def test_ping_database_succeeds_on_reachable_engine() -> None:
    await ping_database(_OkEngine(), timeout_seconds=1.0)  # must not raise


async def test_ping_database_raises_on_unreachable_engine() -> None:
    with pytest.raises(OSError, match="connection refused"):
        await ping_database(_BadEngine(), timeout_seconds=1.0)


async def test_dispose_engine_is_safe() -> None:
    engine = create_engine_from_settings(AppSettings(environment="dev", database_url=_URL))
    assert engine is not None
    await dispose_engine(engine)  # disposes the (never-connected) pool without error

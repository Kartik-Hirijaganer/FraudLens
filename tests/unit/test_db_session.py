"""Unit tests for the async engine factory, sessionmaker, and the readiness ping."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from fraudlens_backend.db.models import Agency, Base
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


@pytest.mark.parametrize("outcome", ["commit", "rollback"])
@pytest.mark.parametrize("storage", ["memory", "file"])
async def test_released_savepoint_remains_owned_by_outer_transaction(
    outcome: str, storage: str, sandbox: Path
) -> None:
    url = (
        "sqlite+aiosqlite://"
        if storage == "memory"
        else f"sqlite+aiosqlite:///{sandbox / 'db.sqlite'}"
    )
    engine = create_engine_from_settings(AppSettings(environment="dev", database_url=url))
    assert engine is not None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = build_sessionmaker(engine)
        async with sessions() as session:
            async with session.begin_nested():
                session.add(Agency(id=uuid.uuid4(), name="Savepoint", slug="savepoint"))
                await session.flush()
            if outcome == "commit":
                await session.commit()
            else:
                await session.rollback()
        async with sessions() as session:
            count = (await session.execute(select(func.count()).select_from(Agency))).scalar_one()
            assert count == (1 if outcome == "commit" else 0)
    finally:
        await dispose_engine(engine)

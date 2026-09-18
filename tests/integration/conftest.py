"""Shared fixtures for integration tests that require independent database connections."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fraudlens_backend.db.models import Base
from fraudlens_backend.settings import AppSettings
from seed import seed  # scripts/ is on sys.path via the root conftest


@pytest.fixture
async def file_db(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    """Create a file-backed SQLite database suitable for background worker sessions."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'integration.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine, async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def seeded(
    db_sessionmaker: async_sessionmaker[AsyncSession], settings: AppSettings
) -> AsyncIterator[AsyncSession]:
    """Yield a session over the seeded foundation (agency, personas, config, baseline rules).

    `settings` resolves to the requesting module's own fixture, so each suite seeds under the
    provider modes it means to exercise while sharing this one body.
    """
    async with db_sessionmaker() as session:
        await seed(session, settings)
        await session.commit()
        yield session

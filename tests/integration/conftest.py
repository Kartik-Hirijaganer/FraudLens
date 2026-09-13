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

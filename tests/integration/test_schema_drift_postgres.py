"""PostgreSQL-only gate: the migrated schema must carry what the ORM models declare.

Every other suite builds its schema with `Base.metadata.create_all`, which reads the models
directly — so a column whose MIGRATION omits a server default still gets one in SQLite, and the
tests validate a schema Alembic never produced. Production runs the migrations instead.

That divergence is not hypothetical. `sar_generation_attempts.created_at` reached production NOT
NULL with no default while `CreatedAtMixin` declared `server_default=func.now()`; SQLAlchemy
relies on that default and omits the column from INSERT, so every write to the table failed with
SQLSTATE 23502 and took down every live investigation whose SAR reached persistence. CI was green
throughout. This gate compares the two sources of truth against a real migrated database, which is
the only place the difference is observable.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fraudlens_backend.db.models import Base

pytestmark = pytest.mark.postgres

_COLUMN_DEFAULTS = text(
    "SELECT table_name, column_name FROM information_schema.columns "
    "WHERE table_schema = current_schema() AND column_default IS NOT NULL"
)


@pytest.fixture
async def postgres_sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a sessionmaker over the migrated CI database, skipping when it is not configured."""
    url = os.environ.get("POSTGRES_TEST_DATABASE_URL")
    if not url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url, pool_pre_ping=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    yield sessionmaker
    await engine.dispose()


async def test_every_model_server_default_survives_the_migrations(
    postgres_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A column the models default must be defaulted in the database the migrations built."""
    declared = {
        (table.name, column.name)
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if column.server_default is not None
    }
    # Guard against a vacuous pass: if the models stop declaring defaults, this gate is worthless
    # and should fail loudly rather than quietly assert nothing.
    assert declared, "no model column declares a server default; this gate would prove nothing"

    async with postgres_sessionmaker() as session:
        rows = (await session.execute(_COLUMN_DEFAULTS)).all()
    migrated = {(row[0], row[1]) for row in rows}

    missing = sorted(declared - migrated)
    assert not missing, (
        "the migrated schema is missing server defaults the models declare, so INSERTs that omit "
        f"these columns will fail against PostgreSQL while passing on SQLite: {missing}"
    )

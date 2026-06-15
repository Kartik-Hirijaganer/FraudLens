"""Alembic migration environment for FraudLens (plan §9.3).

Resolves the database URL at runtime (env var, or a `sqlalchemy.url` set programmatically
by tests) so no connection string is committed, builds an async engine, and runs the
migrations. `target_metadata` is the full ORM metadata — importing the models package
registers every §9 table on `Base.metadata`. `render_as_batch=True` keeps future
migrations portable to SQLite (used by the DB test suite); production/local-demo run on
Postgres (asyncpg).
"""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from fraudlens_backend.db.models import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the URL: explicit config first (tests), then env (prod/local-demo)."""
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    env_url = os.environ.get("DATABASE_URL") or os.environ.get("FRAUDLENS_DATABASE_URL")
    if not env_url:
        raise RuntimeError("DATABASE_URL is not set; cannot run migrations")
    return env_url


def _configure(connection: Connection) -> None:
    """Configure the migration context with batch + type comparison enabled."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode, emitting SQL against the resolved URL."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    """Run migrations within an established (sync) connection."""
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode over an async engine."""
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

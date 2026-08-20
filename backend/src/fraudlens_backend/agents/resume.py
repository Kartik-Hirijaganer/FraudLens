"""Summary: Tenant-scoped resume coordination for bounded SAR-agent workflows.
The coordinator serializes one `(agency_id, run_id)` graph, takes a PostgreSQL
transaction advisory lock in deployed profiles, and loads durable completed
attempts for input-hash replay before any provider access.

Key classes:
- AgentExecutionReplayPort: graph-facing locked replay context protocol.
- AgentExecutionReplay: database-backed advisory-lock and completed-attempt loader.

Key functions:
- execution_replay_context: normalize optional replay coordination for graph and mock paths.

Notes:
- PostgreSQL provides cross-replica serialization; a short-lived keyed local lock also protects
  same-process work and makes SQLite development deterministic without issuing unsupported SQL.
- Invalid persisted payloads fail closed by being omitted from replay, so the affected node runs.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.agents.config import AgentRole
from fraudlens_backend.agents.contracts import AgentExecutionRecord
from fraudlens_backend.db.repositories.agents import (
    AgentExecutionRepository,
    agent_execution_to_record,
)

AgentAttemptKey = tuple[AgentRole, int]
CompletedAgentExecutions = Mapping[AgentAttemptKey, AgentExecutionRecord]
_POSTGRES_DIALECT = "postgresql"


class AgentExecutionReplayPort(Protocol):
    """Provide one serialized, tenant-scoped snapshot of replayable agent attempts."""

    def locked_executions(
        self,
    ) -> AbstractAsyncContextManager[CompletedAgentExecutions]:
        """Hold the run lock while exposing completed attempts keyed by role and attempt."""


@dataclass
class _LocalLockEntry:
    """Reference-counted process-local lock entry for one run identity."""

    lock: asyncio.Lock
    users: int = 0


class _LocalRunLockPool:
    """Serialize same-process resumptions without retaining completed run identities."""

    def __init__(self) -> None:
        """Initialize the guarded keyed-lock registry."""
        self._guard = Lock()
        self._entries: dict[tuple[uuid.UUID, uuid.UUID], _LocalLockEntry] = {}

    @asynccontextmanager
    async def hold(self, key: tuple[uuid.UUID, uuid.UUID]) -> AsyncIterator[None]:
        """Acquire one keyed lock and remove it once all holders and waiters leave."""
        with self._guard:
            entry = self._entries.setdefault(key, _LocalLockEntry(lock=asyncio.Lock()))
            entry.users += 1
        await entry.lock.acquire()
        try:
            yield
        finally:
            entry.lock.release()
            with self._guard:
                entry.users -= 1
                if entry.users == 0:
                    self._entries.pop(key, None)


_LOCAL_RUN_LOCKS = _LocalRunLockPool()


class AgentExecutionReplay:
    """Load completed attempts while holding local and PostgreSQL run serialization."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        agency_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> None:
        """Bind the database factory and verified tenant/run identity."""
        self._sessionmaker = sessionmaker
        self._agency_id = agency_id
        self._run_id = run_id

    @asynccontextmanager
    async def locked_executions(self) -> AsyncIterator[CompletedAgentExecutions]:
        """Serialize the graph and load completed rows before any node can reach a provider."""
        key = (self._agency_id, self._run_id)
        async with _LOCAL_RUN_LOCKS.hold(key), self._sessionmaker() as session:
            if session.get_bind().dialect.name != _POSTGRES_DIALECT:
                yield await self._load(session)
                return
            async with session.begin():
                await _acquire_postgres_advisory_lock(session, *key)
                yield await self._load(session)

    async def _load(self, session: AsyncSession) -> dict[AgentAttemptKey, AgentExecutionRecord]:
        """Return only valid completed records from the verified agency/run scope."""
        rows = await AgentExecutionRepository(
            session,
            self._agency_id,
        ).list_completed_for_run(self._run_id)
        completed: dict[AgentAttemptKey, AgentExecutionRecord] = {}
        for row in rows:
            try:
                record = agent_execution_to_record(row)
            except (TypeError, ValueError, ValidationError):
                continue
            completed[(record.agent, record.attempt)] = record
        return completed


@asynccontextmanager
async def execution_replay_context(
    replay: AgentExecutionReplayPort | None,
) -> AsyncIterator[CompletedAgentExecutions]:
    """Yield an empty replay map when coordination is not configured."""
    if replay is None:
        yield {}
        return
    async with replay.locked_executions() as completed:
        yield completed


async def _acquire_postgres_advisory_lock(
    session: AsyncSession,
    agency_id: uuid.UUID,
    run_id: uuid.UUID,
) -> None:
    """Acquire the transaction-scoped PostgreSQL lock for one tenant/run tuple."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _advisory_lock_key(agency_id, run_id)},
    )


def _advisory_lock_key(agency_id: uuid.UUID, run_id: uuid.UUID) -> int:
    """Derive one stable signed PostgreSQL bigint key from the tenant/run tuple."""
    digest = hashlib.blake2b(
        agency_id.bytes + run_id.bytes,
        digest_size=8,
        person=b"FraudLens",
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)

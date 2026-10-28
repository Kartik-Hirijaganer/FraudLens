"""Summary: Shared, durable daily admission ceiling for public-regulation query embeddings.

Key classes:
- EmbeddingSpend: UTC-day conservative reservation stored in the existing system_config table.
- QueryEmbeddingBudget: reserve worst-case query cost across processes and restarts.

Key functions:
- maximum_query_cost: conservative byte-based cost envelope including transport retries.

Notes:
- This global operational counter is scoped to agency_id IS NULL and contains no case data.
- Byte-count bounds and transport retries are reserved before the provider call, never refunded.
- A missing database, malformed counter, or exhausted ceiling selects lexical fallback.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fraudlens_backend.db.models import SystemConfig

_COUNTER_KEY = "ragEmbeddingSpend"
_COUNTER_ID = uuid.uuid5(uuid.NAMESPACE_URL, _COUNTER_KEY)
_TOKENS_PER_MILLION = Decimal(1_000_000)


class EmbeddingSpend(BaseModel):
    """PHI-free aggregate reservation for one UTC day."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    day: date = Field(..., description="UTC day to which the reservation belongs.")
    reserved_usd: Decimal = Field(..., ge=0, description="Conservatively reserved provider USD.")


class QueryEmbeddingBudget:
    """Deployment-wide ceiling shared by query adapters using the application database."""

    def __init__(self, database_url: str | None, daily_limit_usd: Decimal) -> None:
        """Bind the existing database; defer connections until a live query."""
        self._database_url = database_url
        self._daily_limit = daily_limit_usd
        self._engine: AsyncEngine | None = None

    async def reserve(self, maximum_cost_usd: Decimal) -> None:
        """Upsert the stable global counter and lock it before increasing reservations."""
        if not self._database_url or maximum_cost_usd <= 0:
            raise RuntimeError("embedding budget unavailable")
        if self._engine is None:
            self._engine = create_async_engine(self._database_url, pool_pre_ping=True)
        today = datetime.now(UTC).date()
        insert = sqlite_insert if self._engine.dialect.name == "sqlite" else pg_insert
        initial = EmbeddingSpend(day=today, reserved_usd=Decimal("0"))
        async with self._engine.begin() as connection:
            await connection.execute(
                insert(SystemConfig)
                .values(
                    id=_COUNTER_ID,
                    agency_id=None,
                    key=_COUNTER_KEY,
                    value=initial.model_dump(mode="json"),
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
            scope = (SystemConfig.id == _COUNTER_ID, SystemConfig.agency_id.is_(None))
            raw = (
                await connection.execute(select(SystemConfig.value).where(*scope).with_for_update())
            ).scalar_one()
            spent = EmbeddingSpend.model_validate(raw)
            if spent.day > today:
                raise RuntimeError("embedding budget clock mismatch")
            reserved = spent.reserved_usd if spent.day == today else Decimal("0")
            reserved += maximum_cost_usd
            if reserved > self._daily_limit:
                raise RuntimeError("embedding daily budget exceeded")
            next_spend = EmbeddingSpend(day=today, reserved_usd=reserved)
            await connection.execute(
                update(SystemConfig).where(*scope).values(value=next_spend.model_dump(mode="json"))
            )

    async def close(self) -> None:
        """Release connections on the same event loop that used them."""
        if self._engine is not None:
            await self._engine.dispose()


def maximum_query_cost(text: str, *, input_price: float, attempts: int) -> Decimal:
    """Bound BPE input tokens by UTF-8 bytes and reserve all transport attempts."""
    return (
        Decimal(len(text.encode("utf-8")) + 1)
        * Decimal(str(input_price))
        * attempts
        / _TOKENS_PER_MILLION
    )

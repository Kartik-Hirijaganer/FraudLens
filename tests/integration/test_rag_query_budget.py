"""Query embedding reservations survive restarts and serialize across independent adapters."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from fraudlens_backend.db.models import SystemConfig
from fraudlens_backend.rag.budget import QueryEmbeddingBudget, maximum_query_cost


async def test_daily_reservations_are_shared_and_survive_adapter_restart(file_db) -> None:
    engine, sessions = file_db
    url = engine.url.render_as_string(hide_password=False)
    first = QueryEmbeddingBudget(url, Decimal("0.05"))
    second = QueryEmbeddingBudget(url, Decimal("0.05"))
    try:
        results = await asyncio.gather(
            first.reserve(Decimal("0.03")),
            second.reserve(Decimal("0.03")),
            return_exceptions=True,
        )
        assert sum(result is None for result in results) == 1
        assert sum(isinstance(result, RuntimeError) for result in results) == 1
        await first.close()
        restarted = QueryEmbeddingBudget(url, Decimal("0.05"))
        try:
            await restarted.reserve(Decimal("0.02"))
            with pytest.raises(RuntimeError, match="budget exceeded"):
                await restarted.reserve(Decimal("0.000001"))
        finally:
            await restarted.close()
        async with sessions() as session:
            counter = (
                await session.execute(
                    select(SystemConfig).where(
                        SystemConfig.agency_id.is_(None), SystemConfig.key == "ragEmbeddingSpend"
                    )
                )
            ).scalar_one()
            assert Decimal(counter.value["reserved_usd"]) == Decimal("0.05")
            assert set(counter.value) == {"day", "reserved_usd"}
    finally:
        await first.close()
        await second.close()


@pytest.mark.parametrize("day_delta", [-1, 1])
async def test_budget_rolls_forward_and_rejects_clock_rollback(file_db, day_delta: int) -> None:
    engine, sessions = file_db
    budget = QueryEmbeddingBudget(engine.url.render_as_string(hide_password=False), Decimal("0.05"))
    try:
        await budget.reserve(Decimal("0.05"))
        other_day = datetime.now(UTC).date() + timedelta(days=day_delta)
        async with sessions() as session:
            await session.execute(
                update(SystemConfig)
                .where(SystemConfig.agency_id.is_(None), SystemConfig.key == "ragEmbeddingSpend")
                .values(value={"day": other_day.isoformat(), "reserved_usd": "0.05"})
            )
            await session.commit()
        if day_delta < 0:
            await budget.reserve(Decimal("0.05"))
        else:
            with pytest.raises(RuntimeError, match="clock mismatch"):
                await budget.reserve(Decimal("0.01"))
    finally:
        await budget.close()


async def test_missing_budget_storage_denies_provider_admission() -> None:
    budget = QueryEmbeddingBudget(None, Decimal("0.05"))
    with pytest.raises(RuntimeError, match="unavailable"):
        await budget.reserve(Decimal("0.01"))
    await budget.close()


def test_reservation_bounds_utf8_tokens_and_transport_retries() -> None:
    assert maximum_query_cost("é", input_price=0.02, attempts=3) == Decimal("0.00000018")

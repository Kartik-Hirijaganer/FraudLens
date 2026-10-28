"""PostgreSQL admission proof for the shared query-embedding allowance, without provider calls."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine

from fraudlens_backend.db.models import SystemConfig
from fraudlens_backend.rag.budget import EmbeddingSpend, QueryEmbeddingBudget

pytestmark = pytest.mark.postgres


async def test_postgres_embedding_admission_serializes_and_survives_restart() -> None:
    url = os.environ.get("POSTGRES_TEST_DATABASE_URL")
    if not url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    scope = (SystemConfig.agency_id.is_(None), SystemConfig.key == "ragEmbeddingSpend")
    budgets = [QueryEmbeddingBudget(url, Decimal("0.05")) for _ in range(3)]
    try:
        async with engine.connect() as connection:
            existing = (await connection.execute(select(SystemConfig.id).where(*scope))).first()
        assert existing is None, "Use a fresh disposable PostgreSQL database for this gate"
        try:
            results = await asyncio.gather(
                budgets[0].reserve(Decimal("0.03")),
                budgets[1].reserve(Decimal("0.03")),
                return_exceptions=True,
            )
            assert sum(result is None for result in results) == 1
            assert sum(isinstance(result, RuntimeError) for result in results) == 1
            await budgets[0].close()
            await budgets[2].reserve(Decimal("0.02"))
            with pytest.raises(RuntimeError, match="budget exceeded"):
                await budgets[2].reserve(Decimal("0.000001"))
            async with engine.connect() as connection:
                raw = (
                    await connection.execute(select(SystemConfig.value).where(*scope))
                ).scalar_one()
            spend = EmbeddingSpend.model_validate(raw)
            assert spend.day == datetime.now(UTC).date()
            assert spend.reserved_usd == Decimal("0.05")
            assert set(raw) == {"day", "reserved_usd"}
        finally:
            async with engine.begin() as connection:
                await connection.execute(delete(SystemConfig).where(*scope))
    finally:
        for budget in budgets:
            await budget.close()
        await engine.dispose()

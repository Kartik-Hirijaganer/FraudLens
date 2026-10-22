"""Real PostgreSQL races for CSV acceptance and explicit investigation admission."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from investigation_fakes import _client, _demo_app
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fraudlens_backend.api.deps import AccessClaims, authenticate
from fraudlens_backend.db.models import Agency, AnalysisRun, RunStatus, Transaction

pytestmark = pytest.mark.postgres


@pytest.fixture
async def upload_database() -> AsyncIterator[Any]:
    url = os.environ.get("POSTGRES_TEST_DATABASE_URL")
    if not url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    yield engine, async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_concurrent_uploads_and_reruns_create_one_investigation(
    upload_database: Any, make_settings: Any
) -> None:
    engine, sm = upload_database
    agency_id = uuid.uuid4()
    async with sm() as session:
        session.add(Agency(id=agency_id, name="Upload Race", slug=f"upload-{agency_id.hex}"))
        await session.commit()
    app = _demo_app(make_settings, engine, sm, run_execution_mode="worker")

    async def tenant() -> AccessClaims:
        return AccessClaims(agency_id=str(agency_id), role="admin")

    app.dependency_overrides[authenticate] = tenant
    body = (
        "externalId,amount,currency,occurredAt,originAccount,destAccount,channel,country\n"
        "race,10,USD,2026-01-01T00:00:00Z,1111,2222,wire,US"
    )
    async with _client(app) as client:

        async def upload() -> Any:
            return await client.post(
                "/api/v1/transactions/upload", content=body, headers={"Content-Type": "text/csv"}
            )

        responses = await asyncio.gather(upload(), upload())
        assert [response.status_code for response in responses] == [202, 202]
        assert sum(response.json()["accepted"] for response in responses) == 1
        assert sum(response.json()["duplicates"] for response in responses) == 1
        async with sm() as session:
            run = (
                await session.execute(select(AnalysisRun).where(AnalysisRun.agency_id == agency_id))
            ).scalar_one()
            transaction_id, original_id = run.transaction_id, run.id
            run.status = RunStatus.COMPLETED
            await session.commit()

        async def rerun(key: str) -> Any:
            return await client.post(
                "/api/v1/investigations",
                json={"transactionId": str(transaction_id)},
                headers={"Idempotency-Key": key},
            )

        responses = await asyncio.gather(rerun("rerun"), rerun("rerun"))
        assert responses[0].status_code == responses[1].status_code == 202
        assert responses[0].json() == responses[1].json()
        assert responses[0].json()["runId"] != str(original_id)
        assert (await rerun("different-intent")).status_code == 409
    async with sm() as session:
        assert (
            await session.execute(
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.agency_id == agency_id)
            )
        ).scalar_one() == 1
        assert (
            await session.execute(
                select(func.count())
                .select_from(AnalysisRun)
                .where(AnalysisRun.agency_id == agency_id)
            )
        ).scalar_one() == 2

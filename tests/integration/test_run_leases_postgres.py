"""PostgreSQL-only durable run locking and fencing integration test.

This opt-in suite uses a real PostgreSQL service to prove `FOR UPDATE SKIP LOCKED`: a second worker
cannot claim a row held by the first, then can recover it after lease expiry/backoff while the old
fencing token is rejected. The dedicated CI service supplies POSTGRES_TEST_DATABASE_URL.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fraudlens_backend.db.models import Agency, AnalysisRun, SystemConfig, Transaction
from fraudlens_backend.db.repositories import AnalysisRunRepository
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.runs import LeaseClaim, LeaseLostError, claim_next_run, reap_expired_runs
from fraudlens_backend.runs.admission import reserve_agent_spend

pytestmark = pytest.mark.postgres


@pytest.fixture
async def postgres_sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("POSTGRES_TEST_DATABASE_URL")
    if not url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url, pool_pre_ping=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    yield sessionmaker
    await engine.dispose()


async def test_skip_locked_claim_and_expired_lease_fencing(
    postgres_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    agency_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with postgres_sessionmaker() as session:
        agency = Agency(id=agency_id, name="Postgres Lease Test", slug=f"lease-{agency_id.hex}")
        transaction = Transaction(
            agency_id=agency_id,
            external_id=f"lease-{uuid.uuid4().hex}",
            amount=Decimal("100.00"),
            currency="USD",
            occurred_at=now,
            origin_account="masked-origin",
            dest_account="masked-destination",
            channel="wire",
            country="US",
            features={},
            feature_hash="f" * 64,
        )
        session.add_all([agency, transaction])
        await session.flush()
        run = await AnalysisRunRepository(session, agency_id).create_pending(
            transaction_id=transaction.id,
            deadline_at=now + timedelta(minutes=5),
            request_fingerprint="a" * 64,
        )
        await session.commit()
        run_id = run.id

    locked = asyncio.Event()
    release = asyncio.Event()

    async def hold_first_claim() -> LeaseClaim:
        async with postgres_sessionmaker() as session:
            claim = await claim_next_run(
                session,
                lease_owner="worker-a",
                now=now,
                lease_seconds=1,
            )
            assert claim is not None
            locked.set()
            await release.wait()
            await session.commit()
            return claim

    first_task = asyncio.create_task(hold_first_claim())
    await locked.wait()
    async with postgres_sessionmaker() as session:
        second_while_locked = await claim_next_run(
            session,
            lease_owner="worker-b",
            now=now,
            lease_seconds=1,
        )
        await session.commit()
    assert second_while_locked is None
    release.set()
    first = await first_task

    async with postgres_sessionmaker() as session:
        result = await reap_expired_runs(
            session,
            now=now + timedelta(seconds=2),
            max_attempts=3,
            retry_backoff_seconds=1,
        )
        await session.commit()
    assert result.retried == 1

    async with postgres_sessionmaker() as session:
        replacement = await claim_next_run(
            session,
            lease_owner="worker-b",
            now=now + timedelta(seconds=3),
            lease_seconds=1,
        )
        assert replacement is not None
        repository = AnalysisRunRepository(session, agency_id)
        with pytest.raises(LeaseLostError):
            await repository.require_fence(
                run_id=run_id,
                lease_owner=first.lease_owner,
                fencing_token=first.fencing_token,
            )
        await repository.require_fence(
            run_id=run_id,
            lease_owner=replacement.lease_owner,
            fencing_token=replacement.fencing_token,
        )
        await session.rollback()

    async with postgres_sessionmaker() as session:
        await session.execute(delete(AnalysisRun).where(AnalysisRun.agency_id == agency_id))
        await session.execute(delete(Transaction).where(Transaction.agency_id == agency_id))
        await session.execute(delete(Agency).where(Agency.id == agency_id))
        await session.commit()


async def test_tenant_row_lock_prevents_concurrent_spend_overcommit(
    postgres_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Two replicas may race, but only one can reserve beyond the tenant's remaining budget."""
    agency_id = uuid.uuid4()
    now = datetime.now(UTC)
    run_ids: list[uuid.UUID] = []
    async with postgres_sessionmaker() as session:
        session.add_all(
            [
                Agency(id=agency_id, name="Spend Lock Test", slug=f"spend-{agency_id.hex}"),
                SystemConfig(agency_id=agency_id, key="llmDailyBudgetUsd", value=1),
            ]
        )
        for index in range(2):
            transaction = Transaction(
                agency_id=agency_id,
                external_id=f"spend-{index}-{uuid.uuid4().hex}",
                amount=Decimal("100.00"),
                currency="USD",
                occurred_at=now,
                origin_account="masked-origin",
                dest_account="masked-destination",
                channel="wire",
                country="US",
                features={},
                feature_hash="f" * 64,
            )
            session.add(transaction)
            await session.flush()
            run = await AnalysisRunRepository(session, agency_id).create_pending(
                transaction_id=transaction.id,
                deadline_at=now + timedelta(minutes=5),
                request_fingerprint=f"{index}" * 64,
            )
            run_ids.append(run.id)
        await session.commit()

    async def admit(run_id: uuid.UUID) -> bool:
        async with postgres_sessionmaker() as session:
            run = await AnalysisRunRepository(session, agency_id).get(run_id)
            assert run is not None
            try:
                await reserve_agent_spend(
                    session,
                    run=run,
                    agency_id=agency_id,
                    maximum_attempt_cost_usd=Decimal("0.75"),
                )
            except AppError as error:
                assert error.code == "llm_budget_exceeded"
                await session.rollback()
                return False
            await session.commit()
            return True

    admitted = await asyncio.gather(*(admit(run_id) for run_id in run_ids))
    assert sorted(admitted) == [False, True]

    async with postgres_sessionmaker() as session:
        transactions = list(
            (
                await session.execute(
                    delete(AnalysisRun)
                    .where(AnalysisRun.agency_id == agency_id)
                    .returning(AnalysisRun.transaction_id)
                )
            ).scalars()
        )
        await session.execute(delete(SystemConfig).where(SystemConfig.agency_id == agency_id))
        await session.execute(delete(Transaction).where(Transaction.id.in_(transactions)))
        await session.execute(delete(Agency).where(Agency.id == agency_id))
        await session.commit()

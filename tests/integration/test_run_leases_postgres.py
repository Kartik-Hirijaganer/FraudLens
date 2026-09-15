"""PostgreSQL-only durable run locking, recovery, replay, and admission tests.

This opt-in suite uses a real PostgreSQL service to prove `FOR UPDATE SKIP LOCKED`: a second worker
cannot claim a row held by the first, a replacement resumes a cancelled worker without duplicating
stage outputs, stale fencing is rejected, and spend admission serializes across replicas. The
dedicated CI service supplies POSTGRES_TEST_DATABASE_URL.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from durable_worker_fakes import (
    build_worker,
    fake_pipeline_deps,
    queue_run,
    worker_settings,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fraudlens_backend.runs.worker as worker_module
from fraudlens_backend.api.v1.investigation_stream import EventPolling, event_stream
from fraudlens_backend.db.models import (
    Agency,
    Alert,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunEvent,
    RagRetrieval,
    RunStatus,
    SarDraft,
    SystemConfig,
    Transaction,
)
from fraudlens_backend.db.repositories import AnalysisRunRepository
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.pipeline_runs import PipelineRunStore
from fraudlens_backend.runs import LeaseClaim, LeaseLostError, claim_next_run, reap_expired_runs
from fraudlens_backend.runs.admission import reserve_agent_spend
from fraudlens_backend.settings import AppSettings

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


async def test_cancelled_worker_recovers_once_and_replays_after_api_replacement(
    postgres_sessionmaker: async_sessionmaker[AsyncSession],
    make_settings: Callable[..., AppSettings],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exercise worker failure, takeover, singleton outputs, and persisted SSE on PostgreSQL."""
    agency_id = uuid.uuid4()
    current = datetime.now(UTC)
    run_id = await queue_run(postgres_sessionmaker, agency_id=agency_id, now=current)
    entered = asyncio.Event()
    completion_calls = 0
    complete_run = PipelineRunStore.complete_run

    async def block_first_completion(self: PipelineRunStore, **kwargs: Any) -> None:
        nonlocal completion_calls
        completion_calls += 1
        if completion_calls == 1:
            entered.set()
            await asyncio.Event().wait()
        await complete_run(self, **kwargs)

    monkeypatch.setattr(worker_module, "build_pipeline_deps", fake_pipeline_deps)
    monkeypatch.setattr(PipelineRunStore, "complete_run", block_first_completion)

    def clock() -> datetime:
        return current

    first = build_worker(
        sessionmaker=postgres_sessionmaker,
        settings=worker_settings(make_settings),
        worker_id="worker-a",
        clock=clock,
        heartbeat_file=tmp_path / "postgres-first-heartbeat",
    )
    first_task = asyncio.create_task(first.run_once())
    await entered.wait()
    first_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_task

    current += timedelta(seconds=3)
    second = build_worker(
        sessionmaker=postgres_sessionmaker,
        settings=worker_settings(make_settings),
        worker_id="worker-b",
        clock=clock,
        heartbeat_file=tmp_path / "postgres-second-heartbeat",
    )
    assert not await second.run_once()
    current += timedelta(seconds=1)
    assert await second.run_once()

    async with postgres_sessionmaker() as session:
        run = await session.get(AnalysisRun, run_id)
        stage_counts = {
            model.__tablename__: (
                await session.execute(
                    select(func.count()).select_from(model).where(model.run_id == run_id)
                )
            ).scalar_one()
            for model in (AnalysisResult, RagRetrieval, Alert, SarDraft)
        }
        events = (
            (
                await session.execute(
                    select(AnalysisRunEvent)
                    .where(
                        AnalysisRunEvent.agency_id == agency_id,
                        AnalysisRunEvent.run_id == run_id,
                    )
                    .order_by(AnalysisRunEvent.seq)
                )
            )
            .scalars()
            .all()
        )
    assert run is not None and run.status is RunStatus.COMPLETED
    assert run.attempt == 2 and run.fencing_token == 2
    assert set(stage_counts.values()) == {1}
    assert len(events) == len({event.event_type for event in events})

    replacement_manager = MagicMock()
    replacement_manager.attach.return_value = None
    frames = [
        frame
        async for frame in event_stream(
            manager=replacement_manager,
            sessionmaker=postgres_sessionmaker,
            agency_id=agency_id,
            run_id=run_id,
            after_seq=1,
            polling=EventPolling(
                initial_seconds=0.001,
                maximum_seconds=0.002,
                heartbeat_seconds=0.01,
            ),
        )
    ]
    replayed_ids = [
        int(line.removeprefix("id: "))
        for frame in frames
        for line in frame.splitlines()
        if line.startswith("id: ")
    ]
    expected_ids = [event.seq for event in events if event.seq > 1]
    assert replayed_ids == expected_ids
    assert len(replayed_ids) == len(set(replayed_ids))
    assert any("event: run.completed" in frame for frame in frames)

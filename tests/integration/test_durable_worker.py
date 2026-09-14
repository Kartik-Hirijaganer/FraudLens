"""Durable worker completion and process-replacement integration tests.

These tests drive the real scheduler, lease fencing, pipeline Runner, and persistence adapter over
SQLite while replacing only the model/RAG ports. They prove terminal status + event + lease release
commit together and that a second worker recovers a cancelled first worker after lease expiry.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from pipeline_fakes import (
    FakeExplainerPort,
    FakeRetrieverPort,
    FakeRulesPort,
    FakeSarDrafter,
    FakeScorerPort,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import fraudlens_backend.runs.worker as worker_module
from fraudlens_backend.db.models import (
    Agency,
    AnalysisRun,
    AnalysisRunEvent,
    RunStatus,
    Transaction,
)
from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    ModelRegistryRepository,
    SarDraftRepository,
)
from fraudlens_backend.pipeline_runs import PipelineRunStore
from fraudlens_backend.pipeline_wiring import PipelineComponents
from fraudlens_backend.runs.worker import DurableRunWorker
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskPolicy
from fraudlens_ml.pipeline import PipelineDeps

_AGENCY_ID = uuid.UUID("88888888-8888-4888-8888-888888888888")


async def _queue_run(
    sessionmaker: async_sessionmaker[AsyncSession], *, now: datetime
) -> uuid.UUID:
    """Seed one tenant transaction and queued run."""
    async with sessionmaker() as session:
        session.add(Agency(id=_AGENCY_ID, name="Worker Test", slug="worker-test"))
        transaction = Transaction(
            agency_id=_AGENCY_ID,
            external_id="worker-transaction",
            amount=Decimal("9500.00"),
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
        run = await AnalysisRunRepository(session, _AGENCY_ID).create_pending(
            transaction_id=transaction.id,
            deadline_at=now + timedelta(minutes=5),
            request_fingerprint="a" * 64,
        )
        await session.commit()
        return run.id


def _settings(make_settings: Callable[..., AppSettings]) -> AppSettings:
    return make_settings(
        run_execution_mode="worker",
        run_lease_seconds=2,
        run_heartbeat_seconds=1,
        run_retry_backoff_seconds=1,
    )


async def _fake_deps(**kwargs: Any) -> PipelineDeps:
    """Return deterministic ports around the real fenced persistence adapter."""
    session = cast(AsyncSession, kwargs["session"])
    agency_id = cast(uuid.UUID, kwargs["agency_id"])
    run_id = cast(uuid.UUID, kwargs["run_id"])
    transaction_id = cast(uuid.UUID, kwargs["transaction_id"])
    return PipelineDeps(
        rules=FakeRulesPort(),
        scorer=FakeScorerPort(),
        explainer=FakeExplainerPort(),
        retriever=FakeRetrieverPort(),
        drafter=FakeSarDrafter(),
        store=PipelineRunStore(
            session=session,
            run_id=run_id,
            transaction_id=transaction_id,
            analysis=AnalysisRunRepository(session, agency_id),
            registry=ModelRegistryRepository(session),
            sar=SarDraftRepository(session, agency_id),
            lease_owner=cast(str, kwargs["lease_owner"]),
            fencing_token=cast(int, kwargs["fencing_token"]),
        ),
        emit=kwargs["emit"],
        risk_policy=RiskPolicy(),
    )


def _worker(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: AppSettings,
    worker_id: str,
    clock: Callable[[], datetime],
    heartbeat_file: Path,
) -> DurableRunWorker:
    return DurableRunWorker(
        sessionmaker=sessionmaker,
        components=cast(PipelineComponents, object()),
        settings=settings,
        worker_id=worker_id,
        clock=clock,
        heartbeat_file=heartbeat_file,
    )


async def test_worker_completes_with_terminal_event_and_releases_lease(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    run_id = await _queue_run(db_sessionmaker, now=now)
    monkeypatch.setattr(worker_module, "build_pipeline_deps", _fake_deps)
    heartbeat_file = tmp_path / "heartbeat"
    worker = _worker(
        sessionmaker=db_sessionmaker,
        settings=_settings(make_settings),
        worker_id="worker-a",
        clock=lambda: now,
        heartbeat_file=heartbeat_file,
    )

    assert await worker.run_once()

    async with db_sessionmaker() as session:
        run = await AnalysisRunRepository(session, _AGENCY_ID).get(run_id)
        events = (
            await session.execute(
                select(AnalysisRunEvent)
                .where(
                    AnalysisRunEvent.agency_id == _AGENCY_ID,
                    AnalysisRunEvent.run_id == run_id,
                )
                .order_by(AnalysisRunEvent.seq)
            )
        ).scalars().all()
    assert run is not None and run.status is RunStatus.COMPLETED
    assert run.attempt == 1 and run.fencing_token == 1
    assert run.lease_owner is None and run.lease_expires_at is None
    assert [event.event_type.value for event in events][-1] == "run.completed"
    assert heartbeat_file.exists()


async def test_cancelled_worker_is_recovered_by_second_worker(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    current = datetime(2026, 9, 14, tzinfo=UTC)
    run_id = await _queue_run(db_sessionmaker, now=current)
    entered = asyncio.Event()
    calls = 0

    async def block_first(**kwargs: Any) -> PipelineDeps:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.Event().wait()
        return await _fake_deps(**kwargs)

    monkeypatch.setattr(worker_module, "build_pipeline_deps", block_first)

    def clock() -> datetime:
        return current

    first = _worker(
        sessionmaker=db_sessionmaker,
        settings=_settings(make_settings),
        worker_id="worker-a",
        clock=clock,
        heartbeat_file=tmp_path / "first-heartbeat",
    )
    first_task = asyncio.create_task(first.run_once())
    await entered.wait()
    first_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_task

    current += timedelta(seconds=3)
    second = _worker(
        sessionmaker=db_sessionmaker,
        settings=_settings(make_settings),
        worker_id="worker-b",
        clock=clock,
        heartbeat_file=tmp_path / "second-heartbeat",
    )
    assert not await second.run_once()  # reaper applies the configured retry backoff first
    current += timedelta(seconds=1)
    assert await second.run_once()

    async with db_sessionmaker() as session:
        run = await session.get(AnalysisRun, run_id)
    assert run is not None and run.status is RunStatus.COMPLETED
    assert run.attempt == 2 and run.fencing_token == 2

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
from pathlib import Path
from typing import Any

import pytest
from durable_worker_fakes import (
    build_worker,
    fake_pipeline_deps,
    queue_run,
    worker_settings,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import fraudlens_backend.runs.worker as worker_module
from fraudlens_backend.db.models import (
    Alert,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunEvent,
    RagRetrieval,
    RunStatus,
    SarDraft,
)
from fraudlens_backend.db.repositories import AnalysisRunRepository
from fraudlens_backend.pipeline_runs import PipelineRunStore
from fraudlens_backend.settings import AppSettings

_AGENCY_ID = uuid.UUID("88888888-8888-4888-8888-888888888888")


async def test_worker_completes_with_terminal_event_and_releases_lease(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    claims: list[tuple[str, dict[str, object]]] = []

    class Recorder:
        def info(self, event: str, **fields: object) -> None:
            claims.append((event, fields))

    run_id = await queue_run(db_sessionmaker, agency_id=_AGENCY_ID, now=now)
    monkeypatch.setattr(worker_module, "build_pipeline_deps", fake_pipeline_deps)
    monkeypatch.setattr(worker_module, "get_logger", lambda _name: Recorder())
    heartbeat_file = tmp_path / "heartbeat"
    worker = build_worker(
        sessionmaker=db_sessionmaker,
        settings=worker_settings(make_settings),
        worker_id="worker-a",
        clock=lambda: now,
        heartbeat_file=heartbeat_file,
    )

    assert await worker.run_once()

    async with db_sessionmaker() as session:
        run = await AnalysisRunRepository(session, _AGENCY_ID).get(run_id)
        events = (
            (
                await session.execute(
                    select(AnalysisRunEvent)
                    .where(
                        AnalysisRunEvent.agency_id == _AGENCY_ID,
                        AnalysisRunEvent.run_id == run_id,
                    )
                    .order_by(AnalysisRunEvent.seq)
                )
            )
            .scalars()
            .all()
        )
    assert run is not None and run.status is RunStatus.COMPLETED
    assert run.attempt == 1 and run.fencing_token == 1
    assert run.lease_owner is None and run.lease_expires_at is None
    assert [event.event_type.value for event in events][-1] == "run.completed"
    assert heartbeat_file.exists()
    assert claims == [("investigation.worker_claimed", {"run_id": str(run_id), "attempt": 1})]


async def test_cancelled_worker_is_recovered_by_second_worker(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    current = datetime(2026, 9, 14, tzinfo=UTC)
    run_id = await queue_run(db_sessionmaker, agency_id=_AGENCY_ID, now=current)
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
        sessionmaker=db_sessionmaker,
        settings=worker_settings(make_settings),
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
    second = build_worker(
        sessionmaker=db_sessionmaker,
        settings=worker_settings(make_settings),
        worker_id="worker-b",
        clock=clock,
        heartbeat_file=tmp_path / "second-heartbeat",
    )
    assert not await second.run_once()  # reaper applies the configured retry backoff first
    current += timedelta(seconds=1)
    assert await second.run_once()

    async with db_sessionmaker() as session:
        run = await session.get(AnalysisRun, run_id)
        stage_counts = {
            model.__tablename__: (
                await session.execute(
                    select(func.count()).select_from(model).where(model.run_id == run_id)
                )
            ).scalar_one()
            for model in (AnalysisResult, RagRetrieval, Alert, SarDraft)
        }
        event_types = (
            (
                await session.execute(
                    select(AnalysisRunEvent.event_type).where(
                        AnalysisRunEvent.agency_id == _AGENCY_ID,
                        AnalysisRunEvent.run_id == run_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert run is not None and run.status is RunStatus.COMPLETED
    assert run.attempt == 2 and run.fencing_token == 2
    assert set(stage_counts.values()) == {1}
    assert len(event_types) == len(set(event_types))

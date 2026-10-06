"""Upload worker lifecycle, soft enrichment failure, and queue recovery contracts."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from durable_worker_fakes import fake_pipeline_deps
from investigation_fakes import _client, _demo_app, _seed_demo_transaction
from pipeline_fakes import FakeRetrieverPort, FakeScorerPort
from portfolio_demo_identity import DEMO_AGENCY_ID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import fraudlens_backend.runs.lifespan as lifecycle
import fraudlens_backend.runs.worker as worker_module
from fraudlens_backend.db.models import AnalysisRun, Base, RunStatus
from fraudlens_backend.pipeline_drafting import DeferredUploadDrafter
from fraudlens_backend.runs.leases import claim_next_run
from fraudlens_backend.runs.worker import DurableRunWorker
from fraudlens_backend.sar.budget import SarBudgetExceededError
from fraudlens_backend.settings import AppSettings

_HEADER = "externalId,amount,currency,occurredAt,originAccount,destAccount,channel,country"
_CSV = f"{_HEADER}\nrestart,9500,USD,2026-01-01T00:00:00Z,1111,2222,wire,US"


@pytest.mark.parametrize("outcome", ["live-egress", "budget", "no-alert", "retrieval-failure"])
async def test_optional_failures_preserve_persisted_analysis(
    outcome: str,
    make_settings: Any,
    db_engine: Any,
    db_sessionmaker: Any,
    monkeypatch: pytest.MonkeyPatch,
    sandbox: Path,
) -> None:
    await _seed_demo_transaction(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        upload = await client.post(
            "/api/v1/transactions/upload", content=_CSV, headers={"Content-Type": "text/csv"}
        )
        assert upload.status_code == 202
    builder = AsyncMock(side_effect=SarBudgetExceededError("budget"))

    async def deps(**kwargs: Any) -> Any:
        original = await fake_pipeline_deps(**kwargs)
        if outcome == "no-alert":
            return replace(original, scorer=FakeScorerPort(probability=0.0))
        if outcome == "retrieval-failure":
            return replace(original, retriever=FakeRetrieverPort(error=True))
        return replace(
            original, drafter=DeferredUploadDrafter(builder, live=outcome == "live-egress")
        )

    monkeypatch.setattr(worker_module, "build_pipeline_deps", deps)
    worker = DurableRunWorker(
        sessionmaker=db_sessionmaker,
        components=app.state.pipeline_components,
        settings=app.state.settings,
        worker_id="optional",
        heartbeat_file=sandbox / "heartbeat",
    )
    assert await worker.run_once()
    async with _client(app) as client:
        page = (await client.get("/api/v1/transactions")).json()
        row = next(row for row in page["transactions"] if row["externalId"] == "restart")
        snapshot = (await client.get(f"/api/v1/investigations/{row['latestRunId']}")).json()
        assert snapshot["status"] == "completed" and snapshot["topFeatures"]
        if outcome == "no-alert":
            assert snapshot["alertId"] is None and snapshot["sarDraftId"] is None
        else:
            assert snapshot["alertId"] is not None
        if outcome in {"live-egress", "budget"}:
            assert snapshot["sarErrorCode"] == (
                "egress_source_not_allowed" if outcome == "live-egress" else "llm_budget_exceeded"
            )
            assert (await client.get("/api/v1/transactions/screening")).json()[
                "draftingBlocked"
            ] == 1
        if outcome == "live-egress":
            builder.assert_not_awaited()


async def test_inline_app_restart_recovers_accepted_upload_without_login(
    make_settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    sandbox: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{sandbox / 'runtime.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_demo_transaction(sm)
    app = _demo_app(
        make_settings,
        engine,
        sm,
        run_worker_poll_seconds=0.01,
        run_worker_heartbeat_file=str(sandbox / "heartbeat"),
    )
    async with _client(app) as client:
        assert (
            await client.post(
                "/api/v1/transactions/upload", content=_CSV, headers={"Content-Type": "text/csv"}
            )
        ).status_code == 202
    monkeypatch.setattr(worker_module, "build_pipeline_deps", fake_pipeline_deps)
    try:
        # Acceptance occurred in an earlier API instance; startup alone drains its durable queue.
        async with app.router.lifespan_context(app):
            async with asyncio.timeout(5):
                while True:
                    async with sm() as session:
                        run = (await session.execute(select(AnalysisRun))).scalar_one()
                        if run.status is RunStatus.COMPLETED:
                            break
                    await asyncio.sleep(0.01)
        assert (sandbox / "heartbeat").exists()
    finally:
        await engine.dispose()


async def test_inline_supervisor_recovers_a_scheduler_failure(
    make_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    calls = 0

    async def run(self: Any, stop: asyncio.Event) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("storage unavailable")
        entered.set()
        await stop.wait()

    monkeypatch.setattr(lifecycle.DurableRunWorker, "run_forever", run)
    async with lifecycle.upload_worker_lifespan(
        AsyncMock(), AsyncMock(), make_settings(run_worker_poll_seconds=0.01)
    ):
        await asyncio.wait_for(entered.wait(), timeout=2)
    assert calls == 2


async def test_automatic_claim_deadline_is_not_reset_by_retry(
    make_settings: Any,
    db_engine: Any,
    db_sessionmaker: Any,
) -> None:
    await _seed_demo_transaction(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        await client.post(
            "/api/v1/transactions/upload", content=_CSV, headers={"Content-Type": "text/csv"}
        )
    now = datetime.now(UTC)
    async with db_sessionmaker() as session:
        claim = await claim_next_run(
            session,
            lease_owner="first",
            now=now,
            lease_seconds=10,
            deadline_seconds=60,
            uploads_only=True,
        )
        assert claim is not None and claim.agency_id == DEMO_AGENCY_ID
        run = await session.get(AnalysisRun, claim.run_id)
        run.status = RunStatus.RETRYING
        run.lease_owner = None
        await session.commit()
        retry = await claim_next_run(
            session,
            lease_owner="replacement",
            now=now + timedelta(seconds=20),
            lease_seconds=10,
            deadline_seconds=60,
            uploads_only=True,
        )
        assert retry is not None and retry.deadline_at == claim.deadline_at
        assert retry.attempt == 2


@pytest.mark.parametrize("execution_mode", ["inline", "worker"])
async def test_no_database_or_external_mode_does_not_start_inline_scheduler(
    execution_mode: str,
    make_settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = AsyncMock()
    monkeypatch.setattr(lifecycle, "DurableRunWorker", worker)
    async with lifecycle.upload_worker_lifespan(
        None, AsyncMock(), make_settings(run_execution_mode=execution_mode)
    ):
        worker.assert_not_called()


async def test_production_worker_heartbeat_works_from_read_only_app_directory(
    db_sessionmaker: Any, monkeypatch: pytest.MonkeyPatch, sandbox: Path
) -> None:
    monkeypatch.setenv("FRAUDLENS_ENVIRONMENT", "prod")
    monkeypatch.delenv("FRAUDLENS_RUN_WORKER_HEARTBEAT_FILE", raising=False)
    settings = AppSettings()
    app_dir = sandbox / "app"
    app_dir.mkdir(mode=0o555)
    monkeypatch.chdir(app_dir)
    # Keep the production directory, using a unique filename to avoid touching another worker.
    heartbeat = Path(settings.run_worker_heartbeat_file).with_name(f"{uuid.uuid4()}.heartbeat")
    directory_existed = heartbeat.parent.exists()
    try:
        worker = DurableRunWorker(
            sessionmaker=db_sessionmaker,
            components=AsyncMock(),
            settings=settings,
            worker_id="readonly-app",
            heartbeat_file=heartbeat,
            uploads_only=True,
        )
        assert not await worker.run_once()
        assert heartbeat.is_file()
        assert not (app_dir / ".local").exists()
    finally:
        heartbeat.unlink(missing_ok=True)
        if not directory_existed and heartbeat.parent.exists():
            heartbeat.parent.rmdir()
        app_dir.chmod(0o755)

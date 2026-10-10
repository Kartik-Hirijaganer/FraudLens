"""Behavioral coverage for atomic upload admission, queue draining, and read-only navigation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from durable_worker_fakes import fake_pipeline_deps
from investigation_fakes import _client, _demo_app, _seed_demo_transaction
from portfolio_demo_identity import DEMO_AGENCY_ID
from sqlalchemy import func, select

import fraudlens_backend.api.v1.transactions as transaction_api
import fraudlens_backend.runs.worker as worker_module
from fraudlens_backend.db.models import (
    Agency,
    Alert,
    AnalysisResult,
    AnalysisRun,
    JobExecution,
    RunStatus,
    SarDraft,
    Transaction,
)
from fraudlens_backend.db.repositories import AnalysisRunRepository
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.runs.worker import DurableRunWorker
from fraudlens_backend.services.screening import investigation_summaries, screening_progress

_HEADER = "externalId,amount,currency,occurredAt,originAccount,destAccount,channel,country"
_OTHER = uuid.UUID("66666666-6666-4666-8666-666666666666")


def csv_rows(count: int) -> str:
    """Valid synthetic upload spanning arbitrarily small test batch limits."""
    return (
        _HEADER
        + "\n"
        + "\n".join(
            f"csv-{index},9500,USD,2026-01-01T00:00:00Z,1111,2222,wire,US" for index in range(count)
        )
    )


async def upload(client: Any, body: str) -> Any:
    return await client.post(
        "/api/v1/transactions/upload", content=body, headers={"Content-Type": "text/csv"}
    )


async def test_all_accepted_rows_are_durable_across_limits_and_queue_wait(
    make_settings: Any,
    db_engine: Any,
    db_sessionmaker: Any,
    monkeypatch: pytest.MonkeyPatch,
    sandbox: Path,
) -> None:
    await _seed_demo_transaction(db_sessionmaker)
    app = _demo_app(
        make_settings,
        db_engine,
        db_sessionmaker,
        batch_score_limit=2,
        run_claim_batch=1,
        run_execution_mode="worker",
    )
    async with _client(app) as client:
        result = await upload(client, csv_rows(5))
        assert result.status_code == 202
        body = result.json()
        assert body["screening"]["pending"] == body["accepted"] == 5
        page = (await client.get("/api/v1/transactions")).json()
        uploaded = [row for row in page["transactions"] if row["externalId"].startswith("csv-")]
        assert all(row["investigation"]["status"] == "pending" for row in uploaded)
        assert all(row["latestRunId"] is None for row in uploaded)
        assert (await upload(client, csv_rows(5))).json()["duplicates"] == 5
        assert (await client.get(f"/api/v1/transactions/screening?jobId={body['jobId']}")).json()[
            "total"
        ] == 5
    async with db_sessionmaker() as session:
        runs = (await session.execute(select(AnalysisRun))).scalars().all()
        assert len(runs) == 5
        assert all(run.deadline_at is None and run.agency_id == DEMO_AGENCY_ID for run in runs)
    monkeypatch.setattr(worker_module, "build_pipeline_deps", fake_pipeline_deps)
    # Queue time exceeds the configured run deadline. Every row must still be claimable.
    current = datetime.now(UTC) + timedelta(hours=1)
    worker = DurableRunWorker(
        sessionmaker=db_sessionmaker,
        components=app.state.pipeline_components,
        settings=app.state.settings,
        worker_id="upload-test",
        clock=lambda: current,
        heartbeat_file=sandbox / "heartbeat",
    )
    for _index in range(5):
        assert await worker.run_once()
        current += timedelta(minutes=10)
    assert not await worker.run_once()
    async with db_sessionmaker() as session:
        progress = await screening_progress(session, agency_id=DEMO_AGENCY_ID, poll_after_ms=2000)
        assert progress.total == progress.completed == 5
        assert progress.failed == progress.pending == 0
        for model in (AnalysisRun, AnalysisResult, Alert, SarDraft):
            assert (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one() == 5
    async with _client(app) as client:
        metrics = (await client.get("/api/v1/dashboard/metrics")).json()
        assert metrics["transactions"]["total"] == 6
        assert len((await client.get("/api/v1/alerts")).json()["alerts"]) == 5


async def test_mixed_rows_retries_and_read_only_progress_are_tenant_scoped(
    make_settings: Any, db_engine: Any, db_sessionmaker: Any
) -> None:
    await _seed_demo_transaction(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    body = (
        csv_rows(1)
        + "\n"
        + csv_rows(1).split("\n")[1]
        + "\ninvalid,bad,USD,2026-01-01T00:00:00Z,1,2,wire,US"
    )
    async with _client(app) as client:
        first = (await upload(client, body)).json()
        second = (await upload(client, body)).json()
        assert (first["accepted"], first["duplicates"], first["rejected"]) == (1, 1, 1)
        assert (second["accepted"], second["duplicates"], second["rejected"]) == (0, 2, 1)
        for _index in range(3):
            assert (await client.get("/api/v1/transactions/screening")).json()["total"] == 1
            await client.get("/api/v1/me")
            await client.get("/api/v1/transactions")
    async with db_sessionmaker() as session:
        session.add(Agency(id=_OTHER, name="Other", slug="other-screening"))
        await session.commit()
        assert (await screening_progress(session, agency_id=_OTHER, poll_after_ms=2000)).total == 0
        with pytest.raises(AppError, match="screening_job_not_found"):
            await screening_progress(
                session, agency_id=_OTHER, job_id=uuid.UUID(first["jobId"]), poll_after_ms=2000
            )
        transaction_id = (await session.execute(select(AnalysisRun.transaction_id))).scalar_one()
        assert (
            await investigation_summaries(
                session, agency_id=_OTHER, transaction_ids=[transaction_id]
            )
            == {}
        )
        assert (
            await session.execute(select(func.count()).select_from(AnalysisRun))
        ).scalar_one() == 1


async def test_enqueue_failure_rolls_back_the_entire_acceptance(
    make_settings: Any, db_engine: Any, db_sessionmaker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_demo_transaction(db_sessionmaker)

    async def fail(**_kwargs: Any) -> None:
        raise RuntimeError("injected enqueue failure")

    monkeypatch.setattr(transaction_api, "create_investigation_run", fail)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        with pytest.raises(RuntimeError, match="injected enqueue"):
            await upload(client, csv_rows(2))
    async with db_sessionmaker() as session:
        assert (
            await session.execute(select(func.count()).select_from(Transaction))
        ).scalar_one() == 1
        assert (
            await session.execute(select(func.count()).select_from(JobExecution))
        ).scalar_one() == 0
        assert (
            await session.execute(select(func.count()).select_from(AnalysisRun))
        ).scalar_one() == 0


async def test_saved_run_selection_prefers_active_then_pointer_then_failure(
    db_sessionmaker: Any,
) -> None:
    transaction_id = await _seed_demo_transaction(db_sessionmaker)
    async with db_sessionmaker() as session:
        repo = AnalysisRunRepository(session, DEMO_AGENCY_ID)
        completed = await repo.create_running(transaction_id=transaction_id)
        completed.status = RunStatus.COMPLETED
        transaction = await session.get(Transaction, transaction_id)
        transaction.latest_run_id = completed.id
        failed = await repo.create_running(transaction_id=transaction_id)
        failed.created_at = completed.created_at + timedelta(seconds=1)
        failed.status = RunStatus.FAILED
        failed.error_code = "investigation_failed"
        pending = await repo.create_pending(
            transaction_id=transaction_id,
            deadline_at=datetime.now(UTC) + timedelta(minutes=5),
            request_fingerprint="f" * 64,
        )
        await session.commit()

        async def selected() -> str:
            return (
                await investigation_summaries(
                    session, agency_id=DEMO_AGENCY_ID, transaction_ids=[transaction_id]
                )
            )[transaction_id].run_id

        assert await selected() == str(pending.id)
        await session.delete(pending)
        await session.flush()
        assert await selected() == str(completed.id)
        transaction.latest_run_id = None
        await session.flush()
        assert await selected() == str(failed.id)


@pytest.mark.parametrize("role", ["analyst", "reviewer", "auditor"])
async def test_model_override_is_admin_only(
    role: str, make_settings: Any, db_engine: Any, db_sessionmaker: Any
) -> None:
    transaction_id = await _seed_demo_transaction(db_sessionmaker)
    app = _demo_app(
        make_settings,
        db_engine,
        db_sessionmaker,
        auth_dev_bypass_role=role,
        run_execution_mode="worker",
    )
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/investigations",
            json={"transactionId": str(transaction_id), "modelOverride": "any-label"},
        )
        assert response.status_code == 403
    async with db_sessionmaker() as session:
        assert (
            await session.execute(select(func.count()).select_from(AnalysisRun))
        ).scalar_one() == 0


async def test_active_run_conflict_and_idempotent_explicit_rerun(
    make_settings: Any, db_engine: Any, db_sessionmaker: Any
) -> None:
    transaction_id = await _seed_demo_transaction(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker, run_execution_mode="worker")
    async with _client(app) as client:
        body = {"transactionId": str(transaction_id)}
        first = await client.post(
            "/api/v1/investigations", json=body, headers={"Idempotency-Key": "initial"}
        )
        again = await client.post(
            "/api/v1/investigations", json=body, headers={"Idempotency-Key": "initial"}
        )
        conflict = await client.post(
            "/api/v1/investigations", json=body, headers={"Idempotency-Key": "rerun"}
        )
        assert first.json() == again.json()
        assert (
            conflict.status_code == 409
            and conflict.json()["details"][0]["message"] == first.json()["runId"]
        )
        async with db_sessionmaker() as session:
            run = await session.get(AnalysisRun, uuid.UUID(first.json()["runId"]))
            run.status = RunStatus.COMPLETED
            await session.commit()
        rerun = await client.post(
            "/api/v1/investigations", json=body, headers={"Idempotency-Key": "rerun"}
        )
        assert rerun.status_code == 202 and rerun.json() != first.json()
    async with db_sessionmaker() as session:
        assert (
            await session.execute(select(func.count()).select_from(AnalysisRun))
        ).scalar_one() == 2

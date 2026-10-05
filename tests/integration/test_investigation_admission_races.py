"""Fault-injected cross-replica admission races retain idempotency and reject conflicts."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from investigation_fakes import _client, _demo_app, _seed_demo_transaction
from portfolio_demo_identity import DEMO_AGENCY_ID
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from fraudlens_backend.db.models import AnalysisRun, AuditLog, Base, RunStatus, SystemConfig
from fraudlens_backend.db.repositories import AnalysisRunRepository
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.services.investigation_start import (
    _request_fingerprint,
    create_investigation_run,
)


@pytest.mark.parametrize(
    "race", ["after-lock", "unique-winner", "key-conflict", "unrelated-constraint"]
)
async def test_replica_winner_is_reused_only_for_the_same_request(
    race: str,
    make_settings: Any,
    db_engine: Any,
    db_sessionmaker: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction_id = await _seed_demo_transaction(db_sessionmaker)
    winner = AnalysisRun(
        id=uuid.uuid4(),
        agency_id=DEMO_AGENCY_ID,
        transaction_id=transaction_id,
        status=RunStatus.PENDING,
        request_fingerprint=_request_fingerprint(
            transaction_id=transaction_id, model_override=None, workflow_mode=None
        ),
    )
    if race == "key-conflict":
        winner.request_fingerprint = "different-request"
    lookups = (
        [None, winner]
        if race == "after-lock"
        else [None, None, None if race == "unrelated-constraint" else winner]
    )
    monkeypatch.setattr(
        AnalysisRunRepository, "get_by_idempotency_key", AsyncMock(side_effect=lookups)
    )
    monkeypatch.setattr(
        AnalysisRunRepository,
        "create_pending",
        AsyncMock(side_effect=IntegrityError("insert", {}, Exception("constraint"))),
    )
    app = _demo_app(make_settings, db_engine, db_sessionmaker, run_execution_mode="worker")
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "app": app})
    async with db_sessionmaker() as session:

        async def submit() -> Any:
            return await create_investigation_run(
                manager=app.state.run_manager,
                session=session,
                settings=app.state.settings,
                tenant=TenantContext(agency_id=str(DEMO_AGENCY_ID), role="admin"),
                request=request,
                transaction_id=transaction_id,
                idempotency_key="replica-intent",
            )

        if race == "key-conflict":
            with pytest.raises(AppError, match="idempotency_key_conflict"):
                await submit()
        elif race == "unrelated-constraint":
            with pytest.raises(IntegrityError):
                await submit()
        else:
            accepted = await submit()
            assert accepted.run.id == winner.id and not accepted.created


@pytest.fixture
async def admission_database(make_settings: Any, sandbox: Path) -> AsyncIterator[Any]:
    """Use the application's SQLite transaction policy with independent file connections."""
    engine = create_engine_from_settings(
        make_settings(database_url=f"sqlite+aiosqlite:///{sandbox / 'admission.db'}")
    )
    assert engine is not None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield engine, build_sessionmaker(engine)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("execution_mode", ["inline", "worker"])
@pytest.mark.parametrize("rejection", ["budget", "audit"])
async def test_rejected_admission_rolls_back_and_can_be_retried(
    rejection: str,
    execution_mode: str,
    admission_database: Any,
    make_settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions = admission_database
    transaction_id = await _seed_demo_transaction(sessions)
    async with sessions() as session:
        session.add_all(
            [
                SystemConfig(
                    agency_id=DEMO_AGENCY_ID, key="featureFlags", value={"multiAgentSar": True}
                ),
                SystemConfig(agency_id=None, key="llmDailyBudgetUsd", value=5),
            ]
        )
        await session.commit()
    app = _demo_app(
        make_settings,
        engine,
        sessions,
        run_execution_mode=execution_mode,
        llm_mode="live" if rejection == "budget" else "mock",
        multi_agent_sar_enabled=True,
        llm_daily_budget_usd=Decimal("0.000001"),
    )
    start = Mock()
    app.state.run_manager.start = start
    body = {"transactionId": str(transaction_id)}
    headers = {"Idempotency-Key": "retry-rejected-admission"}
    async with _client(app) as client:
        with monkeypatch.context() as fault:
            if rejection == "audit":
                fault.setattr(
                    "fraudlens_backend.services.investigation_start.audit_writer",
                    lambda *_args: AsyncMock(
                        record=AsyncMock(side_effect=RuntimeError("audit unavailable"))
                    ),
                )
                with pytest.raises(RuntimeError, match="audit unavailable"):
                    await client.post("/api/v1/investigations", json=body, headers=headers)
            else:
                response = await client.post("/api/v1/investigations", json=body, headers=headers)
                assert response.status_code == 429
                assert response.json()["code"] == "llm_budget_exceeded"
        start.assert_not_called()
        async with sessions() as session:
            assert (
                await session.execute(select(func.count()).select_from(AnalysisRun))
            ).scalar_one() == 0
            assert (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "investigation.start")
                )
            ).scalar_one() == 0
        app.state.settings = app.state.settings.model_copy(
            update={"llm_daily_budget_usd": Decimal("5")}
        )
        retry = await client.post("/api/v1/investigations", json=body, headers=headers)
        assert retry.status_code == 202, retry.text
        replay = await client.post("/api/v1/investigations", json=body, headers=headers)
        assert replay.json() == retry.json()
    async with sessions() as session:
        runs = (await session.execute(select(AnalysisRun))).scalars().all()
        assert len(runs) == 1
        assert (runs[0].llm_reserved_usd > 0) == (rejection == "budget")
    assert start.call_count == (1 if execution_mode == "inline" else 0)

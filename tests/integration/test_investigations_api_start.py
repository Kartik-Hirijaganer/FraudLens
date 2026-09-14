"""Investigation start, idempotency, authorization, quota, and budget API tests."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import cast

from anyio import CancelScope, create_task_group
from investigation_fakes import (
    _client,
    _demo_app,
    _register_version,
    _seed_demo_transaction,
)
from portfolio_demo_identity import DEMO_AGENCY_ID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from fraudlens_backend.api.v1.investigations import _stream_session
from fraudlens_backend.db.models import (
    AnalysisRun,
    AuditLog,
    SystemConfig,
)
from fraudlens_backend.main import create_app
from fraudlens_backend.settings import AppSettings


async def test_stream_session_finishes_close_when_consumer_is_cancelled() -> None:
    close_started = asyncio.Event()
    allow_close = asyncio.Event()
    close_finished = asyncio.Event()

    class ClosingSession:
        async def close(self) -> None:
            close_started.set()
            await allow_close.wait()
            close_finished.set()

    closing_session = ClosingSession()
    factory = cast(async_sessionmaker[AsyncSession], lambda: closing_session)

    async def consume() -> None:
        async with _stream_session(factory):
            pass

    scopes: list[CancelScope] = []

    async def scoped_consume() -> None:
        with CancelScope() as scope:
            scopes.append(scope)
            await consume()

    async with create_task_group() as task_group:
        task_group.start_soon(scoped_consume)
        await close_started.wait()
        scopes[0].cancel()
        allow_close.set()
        await close_finished.wait()
    assert close_finished.is_set()


async def test_post_unknown_model_override_returns_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    transaction_id = await _seed_demo_transaction(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/investigations",
            json={"transactionId": str(transaction_id), "modelOverride": "no-such-version"},
        )
    assert resp.status_code == 404  # unregistered override rejected before the run starts (§5.4)
    assert resp.json()["code"] == "model_version_not_found"


async def test_post_with_registered_model_override_owns_run(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    transaction_id = await _seed_demo_transaction(db_sessionmaker)
    await _register_version(db_sessionmaker, label="override-cand")
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/investigations",
            json={"transactionId": str(transaction_id), "modelOverride": "override-cand"},
        )
    assert resp.status_code == 202  # a registered override passes the guard and owns a run
    assert resp.json()["runId"]


async def test_post_without_database_returns_503(
    make_settings: Callable[..., AppSettings],
) -> None:
    app = create_app(make_settings(environment="dev", auth_dev_bypass=True))  # no DB wired
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/investigations", json={"transactionId": str(uuid.uuid4())}
        )
    assert resp.status_code == 503  # the DB dependency fails closed before the handler runs
    assert resp.json()["code"] == "service_unavailable"


async def test_stream_without_database_returns_unavailable(
    make_settings: Callable[..., AppSettings],
) -> None:
    app = create_app(make_settings(environment="dev", auth_dev_bypass=True))  # no DB / no manager
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/investigations/{uuid.uuid4()}/stream")
    assert resp.status_code == 503


async def test_auditor_cannot_start_investigation(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    transaction_id = await _seed_demo_transaction(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker, auth_dev_bypass_role="auditor")
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/investigations", json={"transactionId": str(transaction_id)}
        )
    assert resp.status_code == 403
    assert resp.json()["code"] == "role_permission_required"


async def test_analyst_cannot_override_workflow_mode(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Explicit workflow selection is restricted to the admin/evaluation permission."""
    transaction_id = await _seed_demo_transaction(db_sessionmaker, external_id="eval-rbac")
    app = _demo_app(make_settings, db_engine, db_sessionmaker, auth_dev_bypass_role="analyst")

    async with _client(app) as client:
        response = await client.post(
            "/api/v1/investigations",
            json={"transactionId": str(transaction_id), "workflowMode": "single_writer"},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "role_permission_required"


async def test_post_missing_transaction_returns_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_demo_transaction(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/investigations", json={"transactionId": str(uuid.uuid4())}
        )
    assert resp.status_code == 404
    assert resp.json()["code"] == "transaction_not_found"


async def test_post_dedupes_hashed_idempotency_key_across_manager_restart(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    transaction_id = await _seed_demo_transaction(db_sessionmaker)
    first_app = _demo_app(make_settings, db_engine, db_sessionmaker)
    first_app.state.run_manager.start = lambda **_kwargs: None  # stub the background launch
    body = {"transactionId": str(transaction_id)}
    async with _client(first_app) as client:
        first = await client.post(
            "/api/v1/investigations", json=body, headers={"Idempotency-Key": "k1"}
        )

    # A fresh manager/app has no process-local memory; the tenant-scoped DB key still dedupes.
    restarted_app = _demo_app(make_settings, db_engine, db_sessionmaker)
    restarted_app.state.run_manager.start = lambda **_kwargs: None
    async with _client(restarted_app) as client:
        second = await client.post(
            "/api/v1/investigations", json=body, headers={"Idempotency-Key": "k1"}
        )
        third = await client.post("/api/v1/investigations", json=body)  # no key → new run

    assert first.status_code == 202
    assert first.json()["runId"] == second.json()["runId"]  # double-click dedupe
    assert third.json()["runId"] != first.json()["runId"]
    async with db_sessionmaker() as session:
        runs = (await session.execute(select(AnalysisRun))).scalars().all()
        run_count = len(runs)
    assert run_count == 2  # the deduped POST created no extra run
    persisted_key = next(run.idempotency_key for run in runs if run.idempotency_key is not None)
    assert persisted_key != "k1" and len(persisted_key) == 64


async def test_worker_mode_queues_without_starting_process_local_task(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A worker-mode 202 means durable acceptance, not an API-process background task."""
    transaction_id = await _seed_demo_transaction(db_sessionmaker, external_id="queued")
    app = _demo_app(
        make_settings,
        db_engine,
        db_sessionmaker,
        run_execution_mode="worker",
    )
    start_calls: list[object] = []
    app.state.run_manager.start = lambda **kwargs: start_calls.append(kwargs)

    async with _client(app) as client:
        response = await client.post(
            "/api/v1/investigations",
            json={"transactionId": str(transaction_id)},
            headers={"Idempotency-Key": "queued-key"},
        )

    assert response.status_code == 202
    assert start_calls == []
    async with db_sessionmaker() as session:
        run = await session.get(AnalysisRun, uuid.UUID(response.json()["runId"]))
    assert run is not None
    assert run.status.value == "pending"
    assert run.deadline_at is not None
    assert run.request_fingerprint is not None and len(run.request_fingerprint) == 64


async def test_idempotency_key_rejects_changed_investigation_request(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A key cannot silently alias two behaviorally different investigation requests."""
    first_id = await _seed_demo_transaction(db_sessionmaker, external_id="key-first")
    second_id = await _seed_demo_transaction(db_sessionmaker, external_id="key-second")
    app = _demo_app(
        make_settings,
        db_engine,
        db_sessionmaker,
        run_execution_mode="worker",
    )

    async with _client(app) as client:
        first = await client.post(
            "/api/v1/investigations",
            json={"transactionId": str(first_id)},
            headers={"Idempotency-Key": "bound-key"},
        )
        conflict = await client.post(
            "/api/v1/investigations",
            json={"transactionId": str(second_id)},
            headers={"Idempotency-Key": "bound-key"},
        )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_key_conflict"
    assert set(conflict.json()) == {"code", "message", "details", "requestId"}


async def test_live_multi_agent_quota_uses_existing_rate_limited_envelope(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The fourth live graph request from one IP is rejected by the configured daily quota."""
    transaction_id = await _seed_demo_transaction(db_sessionmaker, external_id="quota")
    async with db_sessionmaker() as session:
        session.add_all(
            [
                SystemConfig(
                    agency_id=DEMO_AGENCY_ID,
                    key="featureFlags",
                    value={"multiAgentSar": True},
                ),
                SystemConfig(agency_id=None, key="llmDailyBudgetUsd", value=5),
            ]
        )
        await session.commit()
    app = _demo_app(
        make_settings,
        db_engine,
        db_sessionmaker,
        llm_mode="live",
        multi_agent_sar_enabled=True,
    )
    app.state.run_manager.start = lambda **_kwargs: None

    async with _client(app) as client:
        responses = [
            await client.post(
                "/api/v1/investigations",
                json={
                    "transactionId": str(transaction_id),
                },
            )
            for _index in range(4)
        ]

    assert [response.status_code for response in responses] == [202, 202, 202, 429]
    assert responses[-1].json()["code"] == "rate_limited"
    assert set(responses[-1].json()) == {"code", "message", "details", "requestId"}


async def test_admin_evaluation_bypasses_abuse_quotas_but_keeps_budget_and_audit(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    transaction_id = await _seed_demo_transaction(
        db_sessionmaker, external_id="evaluation-quota-bypass"
    )
    async with db_sessionmaker() as session:
        session.add_all(
            [
                SystemConfig(
                    agency_id=DEMO_AGENCY_ID,
                    key="featureFlags",
                    value={"multiAgentSar": True},
                ),
                SystemConfig(agency_id=None, key="llmDailyBudgetUsd", value=5),
            ]
        )
        await session.commit()
    app = _demo_app(
        make_settings,
        db_engine,
        db_sessionmaker,
        llm_mode="live",
        multi_agent_sar_enabled=True,
    )
    app.state.run_manager.start = lambda **_kwargs: None
    async with _client(app) as client:
        responses = [
            await client.post(
                "/api/v1/investigations",
                json={
                    "transactionId": str(transaction_id),
                    "workflowMode": "multi_agent",
                },
            )
            for _index in range(11)
        ]

    assert {response.status_code for response in responses} == {202}
    async with db_sessionmaker() as session:
        reservations = (await session.execute(select(AnalysisRun.llm_reserved_usd))).scalars().all()
        metadata = (
            (
                await session.execute(
                    select(AuditLog.meta).where(AuditLog.action == "investigation.start")
                )
            )
            .scalars()
            .all()
        )
    assert reservations == [app.state.run_manager.agent_max_cost_usd] * len(responses)
    assert all(value > Decimal("0") for value in reservations)
    assert len(metadata) == len(responses)
    assert all(
        item["evaluationMode"] == "true" and item["evaluationQuotaBypass"] == "true"
        for item in metadata
    )

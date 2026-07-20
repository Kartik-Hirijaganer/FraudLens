"""Phase 12 consistent-audit tests (plan §8.4/§11.7, §16 Phase 12 acceptance: "every mutating
endpoint + model deployment writes an audit row (no PHI)"). Drives each mutating business endpoint
over the in-memory app and asserts a matching `audit_logs` row is written, and that NO audit row's
action/resource_id/metadata carries a PHI-shaped value (re-using the production redaction net to
prove it). The transactions/rules/investigation audit is the Phase-12 addition; the model-deployment
audit (Phase 10) is re-verified here so the consistent-audit property is guarded across routers."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from training_label_fakes import add_matured_training_labels

from fraudlens_backend.db.models import (
    AuditLog,
    JobStatus,
    ModelEvaluation,
    ModelTrainingRun,
    ModelTrigger,
    ModelVersion,
    ModelVersionStatus,
    TrainingDataset,
    Transaction,
)
from fraudlens_backend.main import create_app
from fraudlens_backend.middleware.logging import scrub_text
from fraudlens_backend.settings import AppSettings
from seed import seed

_DEMO_AGENCY_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
_CSV_HEADER = "externalId,amount,currency,occurredAt,originAccount,destAccount,channel,country"


def _build_app(settings: AppSettings, engine: AsyncEngine, sm: async_sessionmaker[AsyncSession]):
    """Build an app wired to the in-memory test engine/sessionmaker (admin dev bypass)."""
    app = create_app(settings)
    app.state.db_engine = engine
    app.state.db_sessionmaker = sm
    return app


def _client(app: object) -> httpx.AsyncClient:
    """An AsyncClient driving the ASGI app in-process (same loop as the DB)."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _txn(**overrides: Any) -> dict[str, Any]:
    """A valid camelCase ingest body."""
    body = {
        "externalId": "AUDIT-1",
        "amount": "100.50",
        "currency": "usd",
        "occurredAt": "2026-01-01T00:00:00+00:00",
        "originAccount": "4111111111111111",
        "destAccount": "987654321",
        "channel": "wire",
        "country": "us",
    }
    body.update(overrides)
    return body


async def _audit_rows(sm: async_sessionmaker[AsyncSession]) -> list[AuditLog]:
    """Return every audit_logs row for the demo agency."""
    async with sm() as session:
        rows = (
            (await session.execute(select(AuditLog).where(AuditLog.agency_id == _DEMO_AGENCY_ID)))
            .scalars()
            .all()
        )
        return list(rows)


def _actions(rows: list[AuditLog]) -> set[str]:
    """The distinct audit actions present."""
    return {row.action for row in rows}


def _assert_no_phi(rows: list[AuditLog]) -> None:
    """Every audit row's action/resource_id/metadata is free of PHI-shaped substrings."""
    for row in rows:
        blob = json.dumps({"a": row.action, "r": row.resource_id, "m": row.meta}, default=str)
        assert scrub_text(blob) == blob, f"PHI-shaped value in audit row {row.action}"


async def test_ingest_endpoints_write_audit_rows(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    csv_body = (
        f"{_CSV_HEADER}\nAUDIT-C1,10.00,usd,2026-01-01T00:00:00+00:00,4111111111111111,9,wire,us"
    )
    async with _client(app) as client:
        created = await client.post("/api/v1/transactions", json=_txn())
        assert created.status_code == 201
        transaction_id = created.json()["transactionId"]
        batch = await client.post(
            "/api/v1/transactions/batch", json={"transactions": [_txn(externalId="AUDIT-B1")]}
        )
        assert batch.status_code == 200
        upload = await client.post(
            "/api/v1/transactions/upload", content=csv_body, headers={"Content-Type": "text/csv"}
        )
        assert upload.status_code == 202
        assert (await client.get("/api/v1/transactions")).status_code == 200
        assert (await client.get(f"/api/v1/transactions/{transaction_id}")).status_code == 200
    rows = await _audit_rows(db_sessionmaker)
    assert {
        "transaction.ingest",
        "transaction.batch_ingest",
        "transaction.csv_import",
        "phi_mask",
        "phi_access",
    } <= _actions(rows)
    _assert_no_phi(rows)


async def test_batch_dry_run_writes_no_audit_row(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/transactions/batch",
            json={"dryRun": True, "transactions": [_txn(externalId="DRY-1")]},
        )
    assert resp.status_code == 200
    # A dry run validates without persisting, so it is not an audited mutation.
    assert "transaction.batch_ingest" not in _actions(await _audit_rows(db_sessionmaker))


async def test_rules_crud_writes_audit_rows(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    rule = {
        "code": "audit_rule",
        "name": "Audit rule",
        "description": "for audit test",
        "ruleType": "velocity",
        "params": {"windowHours": 12, "maxCount": 3},
        "severity": "high",
        "weight": "1.5",
        "enabled": True,
    }
    async with _client(app) as client:
        created = await client.post("/api/v1/rules", json=rule)
        rule_id = created.json()["ruleId"]
        assert (
            await client.patch(f"/api/v1/rules/{rule_id}", json={"enabled": False})
        ).status_code == 200
        assert (await client.delete(f"/api/v1/rules/{rule_id}")).status_code == 204
    rows = await _audit_rows(db_sessionmaker)
    assert {"rule.create", "rule.update", "rule.delete"} <= _actions(rows)
    _assert_no_phi(rows)


async def test_investigation_start_writes_audit_row(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        await seed(session)
        await add_matured_training_labels(session, count=1)
        await session.commit()
        transaction_id = str(
            (
                await session.execute(
                    select(Transaction.id).where(Transaction.agency_id == _DEMO_AGENCY_ID).limit(1)
                )
            ).scalar_one()
        )

    class _NoopManager:
        """Stand-in RunManager whose start() is a no-op (the run is owned + audited before it)."""

        def start(self, **_kwargs: Any) -> None:
            return None

    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    app.state.run_manager = _NoopManager()  # avoid the heavy background pipeline in this audit test
    async with _client(app) as client:
        resp = await client.post("/api/v1/investigations", json={"transactionId": transaction_id})
    assert resp.status_code == 202
    rows = await _audit_rows(db_sessionmaker)
    assert "investigation.start" in _actions(rows)
    _assert_no_phi(rows)


async def test_model_deployment_canary_writes_audit_row(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        await seed(session)
        dataset = TrainingDataset(
            snapshot_query={}, label_window="t", row_count=0, feature_spec={}, content_hash="d" * 64
        )
        session.add(dataset)
        await session.flush()
        run = ModelTrainingRun(
            trigger=ModelTrigger.MANUAL, dataset_id=dataset.id, status=JobStatus.SUCCEEDED
        )
        session.add(run)
        await session.flush()
        version = ModelVersion(
            version_label="cand-audit",
            training_run_id=run.id,
            artifact_uri="cand-audit",
            status=ModelVersionStatus.CANDIDATE,
        )
        session.add(version)
        await session.flush()
        session.add(ModelEvaluation(model_version_id=version.id, metrics={}, passed=True))
        version_id = str(version.id)
        await session.commit()
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        await client.post(f"/api/v1/model-versions/{version_id}/shadow")
        await client.post(f"/api/v1/model-versions/{version_id}/approve")
        canary = await client.post(
            f"/api/v1/model-versions/{version_id}/canary", json={"percent": 25}
        )
    assert canary.status_code == 200
    rows = await _audit_rows(db_sessionmaker)
    assert {"model.shadow", "model.approve", "model.canary"} <= _actions(rows)
    assert any(row.resource_type == "model_deployment" for row in rows)
    _assert_no_phi(rows)

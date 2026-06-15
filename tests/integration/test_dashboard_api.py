"""Phase 12 dashboard-metrics tests (plan §5.3 endpoint 13, §16 Phase 12). Verify the HTTP endpoint
returns the seeded tenant aggregate incl. model health, fails closed without a token, and that the
aggregation is tenant-scoped (a different agency sees none of the demo's activity while the GLOBAL
model-health signals are shared). A repository-level test drives every model-health/cost/risk-band
branch by setting up a canary, an advisory drift report, a SAR cost, an inference log, and a scored
transaction, then asserting the collected aggregate reflects them all."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fraudlens_backend.db.models import (
    AnalysisRun,
    DriftReport,
    JobStatus,
    ModelDeployment,
    ModelInferenceLog,
    ModelTrainingRun,
    ModelTrigger,
    ModelVersion,
    ModelVersionStatus,
    SarDraft,
    SarStatus,
    Severity,
    TrainingDataset,
    Transaction,
)
from fraudlens_backend.db.repositories import DashboardRepository
from fraudlens_backend.main import create_app
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskBand
from seed import seed

_DEMO_AGENCY_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def _build_app(settings: AppSettings, engine: AsyncEngine, sm: async_sessionmaker[AsyncSession]):
    """Build an app wired to the in-memory test engine/sessionmaker."""
    app = create_app(settings)
    app.state.db_engine = engine
    app.state.db_sessionmaker = sm
    return app


def _client(app: object) -> httpx.AsyncClient:
    """An AsyncClient driving the ASGI app in-process (same loop as the DB)."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _seed(sm: async_sessionmaker[AsyncSession]) -> None:
    """Seed the demo dataset (active fixture model + 12 completed runs + ~transactions)."""
    async with sm() as session:
        await seed(session)
        await session.commit()


async def test_metrics_endpoint_reflects_seeded_activity(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.get("/api/v1/dashboard/metrics")
    assert resp.status_code == 200
    body = resp.json()
    # The seed creates 12 completed runs (one per matured label), no alerts, no SARs.
    assert body["runs"]["completed"] == 12
    assert body["runs"]["total"] == 12
    assert body["alerts"]["total"] == 0
    assert body["sar"]["total"] == 0
    assert body["llmCost"]["totalUsd"] == "0"
    assert body["llmCost"]["draftCount"] == 0
    # Seeded transactions are ingested unscored (scoring happens at investigation time).
    assert body["transactions"]["total"] >= 1
    assert body["transactions"]["byRiskBand"]["unscored"] == body["transactions"]["total"]
    # Model health: the active fixture pointer is shared/global, no canary or drift yet.
    assert body["modelHealth"]["activeVersionLabel"] == "v0-fixture"
    assert body["modelHealth"]["canaryVersionLabel"] is None
    assert body["modelHealth"]["canaryPercent"] == 0
    assert body["modelHealth"]["recentInferenceCount"] == 0
    assert body["modelHealth"]["latestDriftSeverity"] is None


async def test_metrics_requires_authentication(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    app = _build_app(make_settings(auth_dev_bypass=False), db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.get("/api/v1/dashboard/metrics")
    assert resp.status_code == 401


async def test_metrics_are_tenant_scoped(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    other_agency = uuid.uuid4()
    async with db_sessionmaker() as session:
        demo = await DashboardRepository(session, _DEMO_AGENCY_ID).collect(as_of=datetime.now(UTC))
        other = await DashboardRepository(session, other_agency).collect(as_of=datetime.now(UTC))
    # The demo tenant sees its seeded activity; a different tenant sees none of it.
    assert demo.run_counts.get("completed") == 12
    assert demo.transaction_total >= 1
    assert other.run_counts == {}
    assert other.transaction_total == 0
    assert other.recent_inference_count == 0
    # Model health (active pointer) is global, so BOTH tenants see the shared active version.
    assert demo.active_version_label == "v0-fixture"
    assert other.active_version_label == "v0-fixture"


async def test_metrics_aggregate_all_signals(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    async with db_sessionmaker() as session:
        deployment = (await session.execute(select(ModelDeployment))).scalar_one()
        run_id = (
            await session.execute(
                select(AnalysisRun.id).where(AnalysisRun.agency_id == _DEMO_AGENCY_ID).limit(1)
            )
        ).scalar_one()
        transaction_id = (
            await session.execute(
                select(Transaction.id).where(Transaction.agency_id == _DEMO_AGENCY_ID).limit(1)
            )
        ).scalar_one()
        # A canary arm (new candidate version pointed to at 25%).
        dataset = TrainingDataset(
            snapshot_query={}, label_window="t", row_count=0, feature_spec={}, content_hash="c" * 64
        )
        session.add(dataset)
        await session.flush()
        run = ModelTrainingRun(
            trigger=ModelTrigger.MANUAL, dataset_id=dataset.id, status=JobStatus.SUCCEEDED
        )
        session.add(run)
        await session.flush()
        canary = ModelVersion(
            version_label="cand-canary",
            training_run_id=run.id,
            artifact_uri="cand-canary",
            status=ModelVersionStatus.CANARY,
        )
        session.add(canary)
        await session.flush()
        deployment.canary_version_id = canary.id
        deployment.canary_percent = 25
        # An advisory drift report on the active version.
        session.add(
            DriftReport(
                model_version_id=deployment.active_version_id,
                window="samples=60",
                metrics={"psi": 0.4},
                severity=Severity.HIGH,
            )
        )
        # A SAR draft with cost (today) + an inference log + a scored transaction.
        session.add(
            SarDraft(
                agency_id=_DEMO_AGENCY_ID,
                run_id=run_id,
                model_id="mock",
                prompt_version="v1",
                prompt_hash="h",
                content="",
                status=SarStatus.DRAFT,
                cost_usd=Decimal("0.010000"),
                created_at=datetime.now(UTC),
            )
        )
        session.add(
            ModelInferenceLog(
                agency_id=_DEMO_AGENCY_ID,
                run_id=run_id,
                model_version_id=deployment.active_version_id,
                was_canary=False,
                fraud_probability=0.5,
                feature_hash="0" * 64,
            )
        )
        scored = await session.get(Transaction, transaction_id)
        scored.risk_band = RiskBand.HIGH
        await session.commit()
        data = await DashboardRepository(session, _DEMO_AGENCY_ID).collect(as_of=datetime.now(UTC))

    assert data.canary_version_label == "cand-canary"
    assert data.canary_percent == 25
    assert data.latest_drift_severity == "high"
    assert data.sar_counts.get("draft") == 1
    assert data.sar_cost_total == Decimal("0.010000")
    assert data.sar_cost_today == Decimal("0.010000")
    assert data.sar_draft_count == 1
    assert data.recent_inference_count == 1
    assert data.transaction_risk_bands.get("high") == 1
    assert data.transaction_risk_bands.get("unscored", 0) == data.transaction_total - 1

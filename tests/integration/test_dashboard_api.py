"""Phase 12 dashboard-metrics tests (plan §5.3 endpoint 13, §16 Phase 12). Verify the HTTP endpoint
returns the seeded tenant aggregate incl. model health, fails closed without a token, and that the
aggregation is tenant-scoped (a different agency sees none of the demo's activity while the GLOBAL
model-health signals are shared). A repository-level test drives every model-health/cost/risk-band
branch by setting up a canary, an advisory drift report, a SAR cost, an inference log, and a scored
transaction, then asserting the collected aggregate reflects them all."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fraudlens_backend.db.models import (
    Alert,
    AlertStatus,
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
from seed import _ALERT_PLAN, _DEMO_LABEL_COUNT, seed

_DEMO_AGENCY_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def _seed_expectations() -> dict[str, Any]:
    """Aggregates the demo seed is expected to produce, derived from its own plan.

    Deriving from `_ALERT_PLAN` / `_DEMO_LABEL_COUNT` (the seed's source of truth) keeps these
    tests correct when the demo seed changes, instead of asserting stale magic numbers.
    """
    alerts_by_status: Counter[str] = Counter()
    sars_by_status: Counter[str] = Counter()
    alert_backed_runs = 0
    for status, _severity, count, sar_status in _ALERT_PLAN:
        alerts_by_status[status.value] += count
        alert_backed_runs += count
        if sar_status is not None:
            sars_by_status[sar_status.value] += count
    return {
        "alerts_by_status": alerts_by_status,
        "sars_by_status": sars_by_status,
        "total_alerts": sum(alerts_by_status.values()),
        "total_sars": sum(sars_by_status.values()),
        # One completed analysis run per matured label AND one per seeded alert.
        "completed_runs": _DEMO_LABEL_COUNT + alert_backed_runs,
    }


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
    exp = _seed_expectations()
    # The seed creates a completed run per matured label AND per lifecycle alert, plus a
    # populated alert queue + SAR drafts (some filed/approved) so the dashboard renders demo data.
    assert body["runs"]["completed"] == exp["completed_runs"]
    assert body["runs"]["total"] == exp["completed_runs"]
    assert body["alerts"]["total"] == exp["total_alerts"]
    assert body["alerts"]["pendingReview"] == exp["alerts_by_status"].get("pending_review", 0)
    assert body["alerts"]["escalated"] == exp["alerts_by_status"].get("escalated", 0)
    assert body["sar"]["total"] == exp["total_sars"]
    # Seeded SAR drafts carry zero LLM cost (no model call), but they DO count as drafts.
    assert Decimal(body["llmCost"]["totalUsd"]) == 0
    assert body["llmCost"]["draftCount"] == exp["total_sars"]
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
    assert demo.run_counts.get("completed") == _seed_expectations()["completed_runs"]
    assert demo.transaction_total >= 1
    assert other.run_counts == {}
    assert other.transaction_total == 0
    assert other.recent_inference_count == 0
    # Model health (active pointer) is global, so BOTH tenants see the shared active version.
    assert demo.active_version_label == "v0-fixture"
    assert other.active_version_label == "v0-fixture"


async def test_metrics_endpoint_maps_new_alert_status_counts(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    async with db_sessionmaker() as session:
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
        session.add_all(
            [
                Alert(
                    agency_id=_DEMO_AGENCY_ID,
                    transaction_id=transaction_id,
                    run_id=run_id,
                    status=AlertStatus.PENDING_REVIEW,
                    severity=Severity.HIGH,
                    review_flags=[{"flag": "low_model_confidence", "reason": "Review required."}],
                ),
                Alert(
                    agency_id=_DEMO_AGENCY_ID,
                    transaction_id=transaction_id,
                    run_id=run_id,
                    status=AlertStatus.ESCALATED,
                    severity=Severity.CRITICAL,
                    review_flags=[],
                ),
            ]
        )
        await session.commit()
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.get("/api/v1/dashboard/metrics")
    assert resp.status_code == 200
    exp = _seed_expectations()
    # One extra alert added per status on top of whatever the seed already created.
    assert (
        resp.json()["alerts"]["pendingReview"]
        == exp["alerts_by_status"].get("pending_review", 0) + 1
    )
    assert resp.json()["alerts"]["escalated"] == exp["alerts_by_status"].get("escalated", 0) + 1


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

    exp = _seed_expectations()
    assert data.canary_version_label == "cand-canary"
    assert data.canary_percent == 25
    assert data.latest_drift_severity == "high"
    # One draft added here on top of the seed's drafts; seeded SARs carry zero cost, so the only
    # spend is the 0.010000 draft added by this test.
    assert data.sar_counts.get("draft") == exp["sars_by_status"].get("draft", 0) + 1
    assert data.sar_cost_total == Decimal("0.010000")
    assert data.sar_cost_today == Decimal("0.010000")
    assert data.sar_draft_count == exp["total_sars"] + 1
    assert data.recent_inference_count == 1
    assert data.transaction_risk_bands.get("high") == 1
    assert data.transaction_risk_bands.get("unscored", 0) == data.transaction_total - 1

"""Phase 10 model-lifecycle API tests (plan §5.3/§5.4, §10.5/§10.5.1, §16 Phase 10 — endpoints
19-26). Verify admin RBAC (non-admin → 403, no token → 401), the retrain trigger (202 + job +
eligibility 422 + in-progress 409), the candidate→shadow→approve→canary→activate state machine
(illegal transitions → 409), 100%→active pointer flip, rollback, the canary auto-abort guard, the
deployment view, and advisory drift listing. Drives the ASGI app in-process over the shared
in-memory engine; candidates are set up via a direct session the handlers then see."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fraudlens_backend.db.models import (
    AnalysisRun,
    DriftReport,
    JobStatus,
    ModelDeployment,
    ModelEvaluation,
    ModelInferenceLog,
    ModelTrainingRun,
    ModelTrigger,
    ModelVersion,
    ModelVersionStatus,
    Severity,
    TrainingDataset,
    TrainingLabel,
)
from fraudlens_backend.main import create_app
from fraudlens_backend.settings import AppSettings
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
    """Seed the demo dataset (active fixture model + balanced matured labels)."""
    async with sm() as session:
        await seed(session)
        await session.commit()


async def _make_candidate(
    sm: async_sessionmaker[AsyncSession], *, label: str, passing: bool = True
) -> str:
    """Create a CANDIDATE version (+ dataset/run/passing-or-failing eval); return its id."""
    async with sm() as session:
        dataset = TrainingDataset(
            snapshot_query={"source": "synthetic"},
            label_window="test",
            row_count=10,
            feature_spec={"features": ["amount_log"]},
            content_hash="x" * 64,
        )
        session.add(dataset)
        await session.flush()
        run = ModelTrainingRun(
            trigger=ModelTrigger.MANUAL, dataset_id=dataset.id, status=JobStatus.SUCCEEDED
        )
        session.add(run)
        await session.flush()
        version = ModelVersion(
            version_label=label,
            training_run_id=run.id,
            artifact_uri=label,
            feature_spec={"features": ["amount_log"]},
            metrics={"pr_auc": 0.61},
            status=ModelVersionStatus.CANDIDATE,
        )
        session.add(version)
        await session.flush()
        session.add(
            ModelEvaluation(model_version_id=version.id, metrics={"pr_auc": 0.61}, passed=passing)
        )
        version_id = str(version.id)
        await session.commit()
        return version_id


# --- admin RBAC -------------------------------------------------------------------------------


async def test_requires_authentication(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    app = _build_app(make_settings(auth_dev_bypass=False), db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.get("/api/v1/training-runs")
    assert resp.status_code == 401


async def test_non_admin_is_forbidden(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    # The dev bypass mints a NON-admin role (configurable), so the admin route fails closed.
    app = _build_app(
        make_settings(auth_dev_bypass=True, auth_dev_bypass_role="analyst"),
        db_engine,
        db_sessionmaker,
    )
    async with _client(app) as client:
        resp = await client.get("/api/v1/training-runs")
    assert resp.status_code == 403
    assert resp.json()["code"] == "admin_role_required"


# --- retrain trigger --------------------------------------------------------------------------


async def test_trigger_retrain_submits_job(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post("/api/v1/training-runs", json={"trigger": "manual"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["jobId"]
    assert body["status"] == "submitted"
    assert body["labelTotal"] == 12


async def test_trigger_insufficient_matured_labels(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        await seed(session)
        await session.execute(delete(TrainingLabel))
        await session.commit()
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post("/api/v1/training-runs")
    assert resp.status_code == 422
    assert resp.json()["code"] == "insufficient_matured_labels"


async def test_trigger_conflicts_when_training_in_progress(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        await seed(session)
        dataset = TrainingDataset(
            snapshot_query={}, label_window="t", row_count=0, feature_spec={}, content_hash="z" * 64
        )
        session.add(dataset)
        await session.flush()
        session.add(
            ModelTrainingRun(
                trigger=ModelTrigger.SCHEDULED, dataset_id=dataset.id, status=JobStatus.RUNNING
            )
        )
        await session.commit()
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post("/api/v1/training-runs")
    assert resp.status_code == 409
    assert resp.json()["code"] == "training_in_progress"


# --- state machine: shadow → approve → canary → activate → rollback ---------------------------


async def test_shadow_requires_passing_evaluation(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    failing = await _make_candidate(db_sessionmaker, label="cand-fail", passing=False)
    passing = await _make_candidate(db_sessionmaker, label="cand-pass", passing=True)
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        blocked = await client.post(f"/api/v1/model-versions/{failing}/shadow")
        ok = await client.post(f"/api/v1/model-versions/{passing}/shadow")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "invalid_model_transition"
    assert ok.status_code == 200
    assert ok.json()["status"] == "shadow"


async def test_approve_blocked_before_shadow(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    candidate = await _make_candidate(db_sessionmaker, label="cand-approve")
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        early = await client.post(f"/api/v1/model-versions/{candidate}/approve")
        await client.post(f"/api/v1/model-versions/{candidate}/shadow")
        approved = await client.post(f"/api/v1/model-versions/{candidate}/approve")
    assert early.status_code == 409  # approve blocked pre-shadow (plan §5.4)
    assert approved.status_code == 200


async def test_canary_requires_approval(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    candidate = await _make_candidate(db_sessionmaker, label="cand-noapprove")
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        await client.post(f"/api/v1/model-versions/{candidate}/shadow")
        resp = await client.post(f"/api/v1/model-versions/{candidate}/canary", json={"percent": 25})
    assert resp.status_code == 409  # canary before approval is illegal


async def test_canary_ramp_then_activate_flips_pointer(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    candidate = await _make_candidate(db_sessionmaker, label="cand-ramp")
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        await client.post(f"/api/v1/model-versions/{candidate}/shadow")
        await client.post(f"/api/v1/model-versions/{candidate}/approve")
        ramp = await client.post(f"/api/v1/model-versions/{candidate}/canary", json={"percent": 25})
        promote = await client.post(
            f"/api/v1/model-versions/{candidate}/canary", json={"percent": 100}
        )
    assert ramp.status_code == 200
    assert ramp.json()["canaryVersionLabel"] == "cand-ramp"
    assert ramp.json()["canaryPercent"] == 25
    assert promote.status_code == 200
    assert promote.json()["activeVersionLabel"] == "cand-ramp"
    assert promote.json()["canaryPercent"] == 0


async def test_rollback_restores_previous_active(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    candidate = await _make_candidate(db_sessionmaker, label="cand-roll")
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        await client.post(f"/api/v1/model-versions/{candidate}/shadow")
        await client.post(f"/api/v1/model-versions/{candidate}/approve")
        await client.post(f"/api/v1/model-versions/{candidate}/canary", json={"percent": 100})
        rollback = await client.post("/api/v1/model-deployment/rollback")
    assert rollback.status_code == 200
    assert rollback.json()["action"] == "restored_previous"
    assert rollback.json()["deployment"]["activeVersionLabel"] == "v0-fixture"


async def test_rollback_with_nothing_to_do_409(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post("/api/v1/model-deployment/rollback")
    assert resp.status_code == 409
    assert resp.json()["code"] == "nothing_to_rollback"


# --- canary auto-abort + deployment view + drift ----------------------------------------------


async def _add_inference_logs(
    sm: async_sessionmaker[AsyncSession], *, version_id: str, was_canary: bool, probability: float
) -> None:
    """Add 25 hash-only inference logs for an arm (enough to clear the min-sample window)."""
    async with sm() as session:
        run_id = (await session.execute(select(AnalysisRun.id).limit(1))).scalar_one()
        for _ in range(25):
            session.add(
                ModelInferenceLog(
                    agency_id=_DEMO_AGENCY_ID,
                    run_id=run_id,
                    model_version_id=uuid.UUID(version_id),
                    was_canary=was_canary,
                    fraud_probability=probability,
                    feature_hash="0" * 64,
                )
            )
        await session.commit()


async def test_canary_auto_abort_on_deviation(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    candidate = await _make_candidate(db_sessionmaker, label="cand-abort")
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    # Find the active fixture version id for its inference arm.
    async with db_sessionmaker() as session:
        active_id = str(
            (await session.execute(select(ModelDeployment))).scalar_one().active_version_id
        )
    async with _client(app) as client:
        await client.post(f"/api/v1/model-versions/{candidate}/shadow")
        await client.post(f"/api/v1/model-versions/{candidate}/approve")
        await client.post(f"/api/v1/model-versions/{candidate}/canary", json={"percent": 50})
        # Active scores low, canary scores high → a large deviation that trips the guard.
        await _add_inference_logs(
            db_sessionmaker, version_id=active_id, was_canary=False, probability=0.1
        )
        await _add_inference_logs(
            db_sessionmaker, version_id=candidate, was_canary=True, probability=0.9
        )
        evaluate = await client.post("/api/v1/model-deployment/canary/evaluate")
    assert evaluate.status_code == 200
    body = evaluate.json()
    assert body["aborted"] is True
    assert body["deployment"]["canaryVersionLabel"] is None  # rolled back


async def test_get_deployment_and_drift_reports(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        deployment = await client.get("/api/v1/model-deployment")
        drift = await client.get("/api/v1/drift-reports")
        runs = await client.get("/api/v1/training-runs")
    assert deployment.status_code == 200
    assert deployment.json()["activeVersionLabel"] == "v0-fixture"
    assert drift.status_code == 200
    assert drift.json()["driftReports"] == []
    assert runs.status_code == 200


async def test_unknown_version_is_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    missing = uuid.uuid4()
    async with _client(app) as client:
        shadow = await client.post(f"/api/v1/model-versions/{missing}/shadow")
        approve = await client.post(f"/api/v1/model-versions/{missing}/approve")
        canary = await client.post(f"/api/v1/model-versions/{missing}/canary", json={"percent": 25})
    for resp in (shadow, approve, canary):
        assert resp.status_code == 404
        assert resp.json()["code"] == "model_version_not_found"


async def test_get_deployment_404_when_unconfigured(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # No seed → no deployment pointer configured.
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.get("/api/v1/model-deployment")
    assert resp.status_code == 404
    assert resp.json()["code"] == "deployment_not_found"


async def test_evaluate_canary_with_no_canary_is_not_aborted(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post("/api/v1/model-deployment/canary/evaluate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["aborted"] is False
    assert body["deviation"] == 0.0
    assert body["deployment"]["canaryVersionLabel"] is None


async def test_drift_reports_lists_advisory_report(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    async with db_sessionmaker() as session:
        active_id = (await session.execute(select(ModelDeployment))).scalar_one().active_version_id
        session.add(
            DriftReport(
                model_version_id=active_id,
                window="samples=60",
                metrics={"psi": 0.42},
                severity=Severity.HIGH,
                advisory=True,
            )
        )
        await session.commit()
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.get("/api/v1/drift-reports")
    assert resp.status_code == 200
    reports = resp.json()["driftReports"]
    assert len(reports) == 1
    assert reports[0]["versionLabel"] == "v0-fixture"
    assert reports[0]["advisory"] is True
    assert reports[0]["severity"] == "high"

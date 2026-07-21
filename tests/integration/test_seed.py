"""Seed tests (plan §16 Phase 2: "seed idempotency"). The seed populates the demo agency,
users, default config, and the active fixture model, and re-running it must not duplicate
rows (the single seed job row is updated in place)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    Agency,
    Alert,
    AlertAction,
    AmlRule,
    AnalysisRun,
    JobExecution,
    ModelDeployment,
    ModelVersion,
    SarDraft,
    SystemConfig,
    TrainingLabel,
    Transaction,
    User,
)
from seed import seed

_COUNTED = (
    Agency,
    User,
    AmlRule,
    SystemConfig,
    ModelVersion,
    ModelDeployment,
    JobExecution,
    Alert,
    AlertAction,
    SarDraft,
    Transaction,
    AnalysisRun,
    TrainingLabel,
)


async def _count(session: AsyncSession, model: type) -> int:
    """Return the row count for a model in the session's database."""
    return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


async def test_seed_creates_expected_entities(db_session: AsyncSession) -> None:
    summary = await seed(db_session)
    assert (summary.agencies, summary.users, summary.model_versions, summary.deployments) == (
        1,
        4,
        1,
        1,
    )
    assert await _count(db_session, Agency) == 1
    assert await _count(db_session, User) == 4
    assert await _count(db_session, ModelVersion) == 1
    assert await _count(db_session, ModelDeployment) == 1


async def test_seed_is_idempotent(db_session: AsyncSession) -> None:
    await seed(db_session)
    first = {model.__name__: await _count(db_session, model) for model in _COUNTED}
    await seed(db_session)
    second = {model.__name__: await _count(db_session, model) for model in _COUNTED}
    assert first == second
    # The single seed job row is updated in place across runs (attempts increments).
    job = (await db_session.execute(select(JobExecution))).scalar_one()
    assert job.attempts == 2


async def test_seed_creates_six_global_baseline_rules(db_session: AsyncSession) -> None:
    summary = await seed(db_session)
    assert summary.rules == 6
    rows = (await db_session.execute(select(AmlRule))).scalars().all()
    assert len(rows) == 6
    assert all(row.agency_id is None for row in rows)  # baseline rules are global (platform)


async def test_active_deployment_points_at_fixture_model(db_session: AsyncSession) -> None:
    await seed(db_session)
    deployment = (await db_session.execute(select(ModelDeployment))).scalar_one()
    version = await db_session.get(ModelVersion, deployment.active_version_id)
    assert version is not None
    assert version.version_label == "v0-fixture"
    assert version.status.value == "active"


async def test_seed_creates_no_operational_evidence(db_session: AsyncSession) -> None:
    await seed(db_session)
    for model in (Transaction, AnalysisRun, TrainingLabel, Alert, AlertAction, SarDraft):
        assert await _count(db_session, model) == 0

"""Seed tests (plan §16 Phase 2: "seed idempotency"). The seed populates the demo agency,
users, default config, and the active fixture model, and re-running it must not duplicate
rows (the single seed job row is updated in place)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    Agency,
    Alert,
    AlertAction,
    AlertStatus,
    AmlRule,
    JobExecution,
    ModelDeployment,
    ModelVersion,
    SarDraft,
    SarStatus,
    Severity,
    SystemConfig,
    TrainingLabel,
    User,
)
from fraudlens_backend.db.repositories import ModelLifecycleRepository
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
)


async def _count(session: AsyncSession, model: type) -> int:
    """Return the row count for a model in the session's database."""
    return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


async def test_seed_creates_expected_entities(db_session: AsyncSession) -> None:
    summary = await seed(db_session)
    assert (summary.agencies, summary.users, summary.model_versions, summary.deployments) == (
        1,
        3,
        1,
        1,
    )
    assert await _count(db_session, Agency) == 1
    assert await _count(db_session, User) == 3
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


async def test_seed_creates_balanced_matured_labels(db_session: AsyncSession) -> None:
    summary = await seed(db_session)
    assert summary.training_labels == 12
    # The seeded labels are matured + balanced, so a retrain is immediately eligible (plan §9.4).
    counts = await ModelLifecycleRepository(db_session).matured_label_counts(
        as_of=datetime.now(UTC)
    )
    assert counts.total == 12
    assert counts.positives == 6 and counts.negatives == 6
    assert await _count(db_session, TrainingLabel) == 12


async def _count_where(session: AsyncSession, model: type, *conditions: object) -> int:
    """Return the row count for a model restricted by the given SQL conditions."""
    stmt = select(func.count()).select_from(model).where(*conditions)
    return int((await session.execute(stmt)).scalar_one())


async def test_seed_creates_populated_alert_queue(db_session: AsyncSession) -> None:
    summary = await seed(db_session)
    # The seed populates a lifecycle of alerts + SAR drafts so the dashboard renders non-empty.
    assert summary.alerts == 29
    assert summary.sar_drafts == 16
    # A real open queue spanning severities → the queue card + severity-mix hint render.
    assert await _count_where(db_session, Alert, Alert.status == AlertStatus.OPEN) == 16
    for severity in (Severity.HIGH, Severity.MEDIUM, Severity.LOW):
        assert (
            await _count_where(
                db_session, Alert, Alert.status == AlertStatus.OPEN, Alert.severity == severity
            )
            >= 1
        )
    # Some SARs are filed (approved) so the "SARs filed" KPI is non-zero.
    assert await _count_where(db_session, SarDraft, SarDraft.status == SarStatus.APPROVED) == 7
    # Non-open alerts carry an append-only triage action (in-review/escalated/resolved/dismissed).
    assert await _count(db_session, AlertAction) == 13

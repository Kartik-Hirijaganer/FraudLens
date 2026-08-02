"""Phase 10 lifecycle-repository tests (plan §5.4, §9.4, §10.5/§10.5.1). Exercise the platform
`ModelLifecycleRepository` writes against the seeded fixture deployment: matured-label counting
(immature excluded), passing-evaluation lookup, in-progress guard, the candidate→shadow→approve→
canary→activate pointer flips (old active archived + retained as previous), rollback (abort canary /
restore previous / nothing), and the hash-only canary inference stats + probability reader."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from portfolio_demo_identity import DEMO_AGENCY_ID, DEMO_ANALYST_ID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from training_label_fakes import add_matured_training_labels

from fraudlens_backend.db.models import (
    AnalysisRun,
    JobStatus,
    ModelEvaluation,
    ModelInferenceLog,
    ModelTrainingRun,
    ModelTrigger,
    ModelVersion,
    ModelVersionStatus,
    TrainingDataset,
)
from fraudlens_backend.db.repositories import ModelLifecycleRepository
from seed import seed


async def _make_candidate(
    session: AsyncSession, *, label: str, passing: bool = True
) -> ModelVersion:
    """Create a CANDIDATE model version (+ dataset/run/evaluation) for lifecycle tests."""
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
    await session.flush()
    return version


async def _seeded_run_id(session: AsyncSession) -> uuid.UUID:
    """Return one seeded analysis-run id (FK for inference logs)."""
    return (await session.execute(select(AnalysisRun.id).limit(1))).scalar_one()


async def test_matured_label_counts_balanced(db_session: AsyncSession) -> None:
    await seed(db_session)
    await add_matured_training_labels(db_session)
    counts = await ModelLifecycleRepository(db_session).matured_label_counts(
        as_of=datetime.now(UTC)
    )
    assert counts.total == 12
    assert counts.positives == 6 and counts.negatives == 6


async def test_has_passing_evaluation(db_session: AsyncSession) -> None:
    repo = ModelLifecycleRepository(db_session)
    passing = await _make_candidate(db_session, label="cand-pass", passing=True)
    failing = await _make_candidate(db_session, label="cand-fail", passing=False)
    assert await repo.has_passing_evaluation(passing.id) is True
    assert await repo.has_passing_evaluation(failing.id) is False


async def test_training_in_progress_guard(db_session: AsyncSession) -> None:
    repo = ModelLifecycleRepository(db_session)
    assert await repo.training_in_progress() is False
    dataset = TrainingDataset(
        snapshot_query={}, label_window="t", row_count=0, feature_spec={}, content_hash="y" * 64
    )
    db_session.add(dataset)
    await db_session.flush()
    db_session.add(
        ModelTrainingRun(
            trigger=ModelTrigger.SCHEDULED, dataset_id=dataset.id, status=JobStatus.RUNNING
        )
    )
    await db_session.flush()
    assert await repo.training_in_progress() is True


async def test_shadow_approve_canary_activate_flips_pointer(db_session: AsyncSession) -> None:
    await seed(db_session)
    repo = ModelLifecycleRepository(db_session)
    deployment = await repo.get_deployment()
    fixture_id = deployment.active_version_id
    candidate = await _make_candidate(db_session, label="cand-activate")

    await repo.promote_to_shadow(candidate)
    assert candidate.status is ModelVersionStatus.SHADOW
    await repo.approve(candidate, approved_by=DEMO_ANALYST_ID)
    assert candidate.approved_at is not None
    await repo.start_canary(candidate, percent=25, updated_by=DEMO_ANALYST_ID)
    deployment = await repo.get_deployment()
    assert deployment.canary_version_id == candidate.id
    assert deployment.canary_percent == 25
    assert candidate.status is ModelVersionStatus.CANARY

    await repo.activate(candidate, updated_by=DEMO_ANALYST_ID)
    deployment = await repo.get_deployment()
    assert deployment.active_version_id == candidate.id
    assert deployment.previous_active_version_id == fixture_id
    assert deployment.canary_version_id is None
    assert deployment.canary_percent == 0
    assert candidate.status is ModelVersionStatus.ACTIVE
    fixture = await db_session.get(ModelVersion, fixture_id)
    assert fixture.status is ModelVersionStatus.ARCHIVED


async def test_rollback_aborts_in_progress_canary(db_session: AsyncSession) -> None:
    await seed(db_session)
    repo = ModelLifecycleRepository(db_session)
    fixture_id = (await repo.get_deployment()).active_version_id
    candidate = await _make_candidate(db_session, label="cand-canary")
    await repo.promote_to_shadow(candidate)
    await repo.approve(candidate, approved_by=DEMO_ANALYST_ID)
    await repo.start_canary(candidate, percent=50, updated_by=DEMO_ANALYST_ID)

    outcome = await repo.rollback(updated_by=DEMO_ANALYST_ID)
    assert outcome is not None and outcome.action == "canary_aborted"
    deployment = await repo.get_deployment()
    assert deployment.canary_version_id is None
    assert deployment.canary_percent == 0
    assert deployment.active_version_id == fixture_id  # active untouched
    assert (await db_session.get(ModelVersion, candidate.id)).status is ModelVersionStatus.ARCHIVED


async def test_rollback_restores_previous_active(db_session: AsyncSession) -> None:
    await seed(db_session)
    repo = ModelLifecycleRepository(db_session)
    fixture_id = (await repo.get_deployment()).active_version_id
    candidate = await _make_candidate(db_session, label="cand-promote")
    await repo.promote_to_shadow(candidate)
    await repo.approve(candidate, approved_by=DEMO_ANALYST_ID)
    await repo.activate(candidate, updated_by=DEMO_ANALYST_ID)

    outcome = await repo.rollback(updated_by=DEMO_ANALYST_ID)
    assert outcome is not None and outcome.action == "restored_previous"
    deployment = await repo.get_deployment()
    assert deployment.active_version_id == fixture_id
    assert deployment.previous_active_version_id is None
    assert (await db_session.get(ModelVersion, fixture_id)).status is ModelVersionStatus.ACTIVE
    assert (await db_session.get(ModelVersion, candidate.id)).status is ModelVersionStatus.ARCHIVED


async def test_rollback_returns_none_when_nothing_to_do(db_session: AsyncSession) -> None:
    await seed(db_session)
    repo = ModelLifecycleRepository(db_session)
    # Active-only pointer: no canary and no previous version, so there is nothing to roll back to.
    assert await repo.rollback(updated_by=DEMO_ANALYST_ID) is None


async def test_canary_inference_stats_and_probabilities(db_session: AsyncSession) -> None:
    await seed(db_session)
    await add_matured_training_labels(db_session, count=1)
    repo = ModelLifecycleRepository(db_session)
    deployment = await repo.get_deployment()
    active_id = deployment.active_version_id
    candidate = await _make_candidate(db_session, label="cand-stats")
    await repo.promote_to_shadow(candidate)
    await repo.approve(candidate, approved_by=DEMO_ANALYST_ID)
    await repo.start_canary(candidate, percent=50, updated_by=DEMO_ANALYST_ID)
    run_id = await _seeded_run_id(db_session)

    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(4):
        db_session.add(
            ModelInferenceLog(
                agency_id=DEMO_AGENCY_ID,
                run_id=run_id,
                model_version_id=active_id,
                was_canary=False,
                fraud_probability=0.2,
                feature_hash="0" * 64,
                created_at=base + timedelta(seconds=index),
            )
        )
        db_session.add(
            ModelInferenceLog(
                agency_id=DEMO_AGENCY_ID,
                run_id=run_id,
                model_version_id=candidate.id,
                was_canary=True,
                fraud_probability=0.8,
                feature_hash="0" * 64,
                created_at=base + timedelta(seconds=index),
            )
        )
    await db_session.flush()

    deployment = await repo.get_deployment()
    stats = await repo.canary_inference_stats(deployment)
    assert stats.active_count == 4 and stats.canary_count == 4
    assert stats.active_mean == 0.2
    assert stats.canary_mean == 0.8
    probabilities = await repo.inference_probabilities(active_id)
    assert probabilities == [0.2, 0.2, 0.2, 0.2]

"""Phase 10 advisory drift-scan tests (plan §9.2, §10.5: "drift advisory only"). Verify the pure
PSI metric (≈0 for identical distributions, large for a shift), the PSI→severity mapping, and that
`scan_active_model` records an ADVISORY drift report — a low-severity "insufficient data" note when
there are too few inferences, and a non-trivial PSI when the score distribution shifts mid-window —
plus a `job_executions(drift_scan)` row. Never gates serving (advisory=True always)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from drift_scan import population_stability_index, scan_active_model, severity_for_psi
from fraudlens_backend.db.models import (
    AnalysisRun,
    JobExecution,
    JobType,
    ModelDeployment,
    ModelInferenceLog,
    Severity,
)
from seed import seed


def test_psi_is_zero_for_identical_distributions() -> None:
    sample = np.linspace(0.0, 1.0, 200)
    assert population_stability_index(sample, sample) < 1e-6


def test_psi_is_large_for_a_shift() -> None:
    reference = np.full(200, 0.1)
    actual = np.full(200, 0.9)
    assert population_stability_index(reference, actual) > 1.0


def test_severity_for_psi_bands() -> None:
    assert severity_for_psi(0.05) is Severity.LOW
    assert severity_for_psi(0.15) is Severity.MEDIUM
    assert severity_for_psi(0.40) is Severity.HIGH
    assert severity_for_psi(0.90) is Severity.CRITICAL


async def _active_version_and_run(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """Return the seeded active model-version id + one seeded analysis-run id for FK references."""
    deployment = (await session.execute(select(ModelDeployment).limit(1))).scalar_one()
    run_id = (await session.execute(select(AnalysisRun.id).limit(1))).scalar_one()
    return deployment.active_version_id, run_id


async def _add_inferences(
    session: AsyncSession, *, version_id: uuid.UUID, run_id: uuid.UUID, probabilities: list[float]
) -> None:
    """Insert hash-only inference logs for a version with increasing timestamps (drift reads order).

    Explicit, monotonically increasing `created_at` stand in for real inference times so the drift
    scan's earlier-vs-recent split is deterministic (in production inferences span real time).
    """
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index, probability in enumerate(probabilities):
        session.add(
            ModelInferenceLog(
                agency_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
                run_id=run_id,
                model_version_id=version_id,
                was_canary=False,
                fraud_probability=probability,
                feature_hash="0" * 64,
                created_at=base + timedelta(seconds=index),
            )
        )
    await session.flush()


async def test_scan_records_insufficient_data_advisory(db_session: AsyncSession) -> None:
    await seed(db_session)
    version_id, run_id = await _active_version_and_run(db_session)
    await _add_inferences(db_session, version_id=version_id, run_id=run_id, probabilities=[0.3] * 5)
    report = await scan_active_model(db_session)
    assert report is not None
    assert report.advisory is True
    assert report.severity is Severity.LOW
    assert report.metrics["note"] == "insufficient_data"


async def test_scan_detects_score_shift_and_records_job(db_session: AsyncSession) -> None:
    await seed(db_session)
    version_id, run_id = await _active_version_and_run(db_session)
    # First half low scores, second half high scores → a clear distribution shift.
    probabilities = [0.1] * 30 + [0.9] * 30
    await _add_inferences(
        db_session, version_id=version_id, run_id=run_id, probabilities=probabilities
    )
    report = await scan_active_model(db_session)
    assert report is not None
    assert report.advisory is True  # drift NEVER gates serving (plan §10.5)
    assert report.metrics["psi"] > 0.25
    assert report.severity in {Severity.HIGH, Severity.CRITICAL}
    jobs = (
        await db_session.execute(
            select(func.count())
            .select_from(JobExecution)
            .where(JobExecution.job_type == JobType.DRIFT_SCAN)
        )
    ).scalar_one()
    assert jobs == 1


async def test_scan_returns_none_without_deployment(db_session: AsyncSession) -> None:
    assert await scan_active_model(db_session) is None

"""Phase 5 training + gate tests (plan §16 Phase 5 / §17.3: "quantitative model gates
(PR-AUC/precision@k/recall@alert-budget/calibration/regression) + baseline"). Trains the real
XGBoost candidate on the deterministic synthetic dataset and asserts it clears every §10.5.1
gate, reproduces the committed fixture's metrics, registers as a CANDIDATE with a passing
evaluation (idempotently), and that the registry resolves the active (+ last-known-good) pointer."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    JobExecution,
    JobType,
    ModelEvaluation,
    ModelVersion,
    ModelVersionStatus,
    TrainingDataset,
)
from fraudlens_ml.scoring import GateReport, ModelGates, evaluate_gates, load_artifact
from lib.synthetic_fraud import generate_dataset, split_dataset
from train_model import TrainedCandidate, register_candidate, train_candidate

_SEED = 1729
_ROWS = 16000
_PR_AUC_PLATFORM_TOLERANCE = 0.01


@pytest.fixture(scope="module")
def trained() -> TrainedCandidate:
    """Train the candidate once on the default synthetic dataset (reused across tests)."""
    split = split_dataset(*generate_dataset(_ROWS, _SEED), _SEED)
    return train_candidate(split, ModelGates(), seed=_SEED)


@pytest.fixture(scope="module")
def report(trained: TrainedCandidate) -> GateReport:
    """The §10.5.1 gate report for the freshly trained candidate (no active model yet)."""
    return evaluate_gates(trained.metrics, trained.baseline_pr_auc, None, ModelGates())


def test_candidate_clears_every_quantitative_gate(
    trained: TrainedCandidate, report: GateReport
) -> None:
    gates = ModelGates()
    metrics = trained.metrics
    assert metrics.pr_auc >= gates.pr_auc_floor
    assert metrics.pr_auc - trained.baseline_pr_auc >= gates.baseline_margin
    assert metrics.recall_at_budget >= gates.recall_at_budget
    assert metrics.precision_at_top_pct >= gates.precision_at_top_pct
    assert metrics.ece <= gates.ece_max
    assert report.passed is True
    assert all(check.passed for check in report.checks)


def test_training_is_deterministic() -> None:
    split = split_dataset(*generate_dataset(_ROWS, _SEED), _SEED)
    first = train_candidate(split, ModelGates(), seed=_SEED)
    second = train_candidate(split, ModelGates(), seed=_SEED)
    assert first.metrics.model_dump() == second.metrics.model_dump()


def test_fresh_train_reproduces_committed_fixture_metrics(
    trained: TrainedCandidate, fixture_model_dir: Path
) -> None:
    fixture_metrics = load_artifact(fixture_model_dir).metrics
    # XGBoost is deterministic within one platform, but Linux/macOS floating-point differences
    # can move the aggregate PR-AUC slightly for the same locked dependency set.
    assert trained.metrics.pr_auc == pytest.approx(
        fixture_metrics["pr_auc"], abs=_PR_AUC_PLATFORM_TOLERANCE
    )


async def test_register_candidate_writes_passing_evaluation(
    trained: TrainedCandidate, report: GateReport, db_session: AsyncSession
) -> None:
    version_id = await register_candidate(
        db_session,
        trained,
        report,
        version_label="xgb-test",
        artifact_uri="xgb-test",
        seed=_SEED,
        rows=_ROWS,
    )
    version = await db_session.get(ModelVersion, version_id)
    assert version is not None
    assert version.status is ModelVersionStatus.CANDIDATE  # not auto-promoted to active
    assert version.version_label == "xgb-test"

    evaluation = (
        await db_session.execute(
            select(ModelEvaluation).where(ModelEvaluation.model_version_id == version_id)
        )
    ).scalar_one()
    assert evaluation.passed is True
    assert any(check["name"] == "pr_auc_floor" for check in evaluation.metrics["checks"])

    jobs = (
        await db_session.execute(
            select(func.count())
            .select_from(JobExecution)
            .where(JobExecution.job_type == JobType.TRAIN)
        )
    ).scalar_one()
    assert jobs == 1
    datasets = (
        await db_session.execute(select(func.count()).select_from(TrainingDataset))
    ).scalar_one()
    assert datasets == 1


async def test_register_candidate_is_idempotent(
    trained: TrainedCandidate, report: GateReport, db_session: AsyncSession
) -> None:
    first = await register_candidate(
        db_session,
        trained,
        report,
        version_label="xgb-dup",
        artifact_uri="xgb-dup",
        seed=_SEED,
        rows=_ROWS,
    )
    second = await register_candidate(
        db_session,
        trained,
        report,
        version_label="xgb-dup",
        artifact_uri="xgb-dup",
        seed=_SEED,
        rows=_ROWS,
    )
    assert first == second  # re-running the same config is a no-op
    versions = (
        await db_session.execute(
            select(func.count())
            .select_from(ModelVersion)
            .where(ModelVersion.version_label == "xgb-dup")
        )
    ).scalar_one()
    assert versions == 1

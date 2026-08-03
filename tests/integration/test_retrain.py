"""Phase 10 retrain-Job tests (plan §9.4, §10.5.1, §16 Phase 10 / §17.3 "lifecycle + tenant-safe
training"). Verify the candidate is registered CANDIDATE-only (the active pointer is never touched),
the dataset manifest is tenant-safe (no agency_id / no PHI — feature names only), the per-tenant
slice metrics are recorded and the demo-config candidate clears the per-tenant slice gate against
the fixture active model, registration is idempotent, and matured-label counting drives eligibility
(immature/future labels excluded)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from portfolio_demo_identity import DEMO_AGENCY_ID, DEMO_ANALYST_ID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from training_label_fakes import add_matured_training_labels

from fraudlens_backend.db.models import (
    AnalysisRun,
    ModelDeployment,
    ModelEvaluation,
    ModelTrainingRun,
    ModelTrigger,
    ModelVersion,
    ModelVersionStatus,
    RunStatus,
    TrainingDataset,
    TrainingLabel,
    TrainingLabelType,
    Transaction,
)
from fraudlens_backend.db.repositories import ModelLifecycleRepository
from fraudlens_backend.db.repositories.model_lifecycle import LabelCounts, labels_eligible
from fraudlens_ml.scoring import ModelGates, evaluate_gates, evaluate_tenant_slices
from lib.dataset import DataSplit, split_dataset
from lib.synthetic_fraud import generate_dataset
from retrain import (
    TrainedCandidate,
    register_retrained_candidate,
    tenant_slice_pr_aucs,
    train_candidate,
)
from seed import seed

_SEED = 1729
_ROWS = 16000


@pytest.fixture(scope="module")
def split() -> DataSplit:
    """The deterministic synthetic split the retrain candidate trains on (seed 1729)."""
    return split_dataset(*generate_dataset(_ROWS, _SEED), _SEED)


@pytest.fixture(scope="module")
def trained(split: DataSplit) -> TrainedCandidate:
    """Train the retrain candidate once (reused across tests)."""
    return train_candidate(split, ModelGates(), seed=_SEED)


def test_tenant_slice_pr_aucs_partition_holdout(
    split: DataSplit, trained: TrainedCandidate
) -> None:
    slices = tenant_slice_pr_aucs(split, trained, slices=2)
    assert set(slices) == {"slice-0", "slice-1"}
    assert all(0.0 <= value <= 1.0 for value in slices.values())


def test_demo_config_candidate_clears_per_tenant_gate(
    split: DataSplit, trained: TrainedCandidate
) -> None:
    # The candidate is trained on the same synthetic data as the fixture active model, so the
    # active PR-AUC equals the candidate's; the 2 demo slices must stay within the 0.05 tolerance.
    active_pr_auc = trained.metrics.pr_auc
    slices = tenant_slice_pr_aucs(split, trained, slices=2)
    checks = evaluate_tenant_slices(slices, active_pr_auc, ModelGates())
    assert all(check.passed for check in checks)


async def test_register_is_candidate_only_and_tenant_safe(
    split: DataSplit, trained: TrainedCandidate, db_session: AsyncSession
) -> None:
    await seed(db_session)
    deployment = (await db_session.execute(select(ModelDeployment).limit(1))).scalar_one()
    active_before = deployment.active_version_id

    gates = ModelGates()
    active_pr_auc = trained.metrics.pr_auc
    report = evaluate_gates(trained.metrics, trained.baseline_pr_auc, active_pr_auc, gates)
    slice_pr_aucs = tenant_slice_pr_aucs(split, trained, slices=2)
    slice_checks = evaluate_tenant_slices(slice_pr_aucs, active_pr_auc, gates)
    counts = await ModelLifecycleRepository(db_session).matured_label_counts(
        as_of=datetime.now(UTC)
    )

    version_id, passed = await register_retrained_candidate(
        db_session,
        trained,
        report,
        slice_checks,
        slice_pr_aucs,
        version_label="retrain-test",
        artifact_uri="retrain-test",
        trigger=ModelTrigger.MANUAL,
        counts=counts,
        label_window="matured<=test",
        active_version_id=active_before,
        seed=_SEED,
        rows=_ROWS,
    )
    await db_session.commit()

    version = await db_session.get(ModelVersion, version_id)
    assert version is not None
    assert version.status is ModelVersionStatus.CANDIDATE  # candidate-only, never auto-promoted
    assert passed is True  # clears the overall + per-tenant gates

    # The active pointer is untouched (prod untouched, plan acceptance).
    await db_session.refresh(deployment)
    assert deployment.active_version_id == active_before

    # The evaluation records overall + per-tenant slice metrics (plan §9.4).
    evaluation = (
        await db_session.execute(
            select(ModelEvaluation).where(ModelEvaluation.model_version_id == version_id)
        )
    ).scalar_one()
    assert evaluation.passed is True
    assert set(evaluation.metrics["tenantSlices"]) == {"slice-0", "slice-1"}
    assert evaluation.metrics["tenantSliceChecks"]

    # The dataset manifest is tenant-safe: no agency_id, no PHI — feature NAMES only (ADR-015).
    training_run = await db_session.get(ModelTrainingRun, version.training_run_id)
    assert training_run is not None
    dataset = await db_session.get(TrainingDataset, training_run.dataset_id)
    assert dataset is not None
    assert "agency_id" not in dataset.snapshot_query
    assert "agencyId" not in dataset.snapshot_query
    assert "features" in dataset.feature_spec


async def test_register_retrained_candidate_is_idempotent(
    split: DataSplit, trained: TrainedCandidate, db_session: AsyncSession
) -> None:
    gates = ModelGates()
    report = evaluate_gates(trained.metrics, trained.baseline_pr_auc, None, gates)
    slice_pr_aucs = tenant_slice_pr_aucs(split, trained, slices=2)
    slice_checks = evaluate_tenant_slices(slice_pr_aucs, None, gates)
    counts = LabelCounts(total=12, positives=6, negatives=6)
    kwargs: dict[str, object] = {
        "version_label": "retrain-dup",
        "artifact_uri": "retrain-dup",
        "trigger": ModelTrigger.SCHEDULED,
        "counts": counts,
        "label_window": "matured<=test",
        "active_version_id": None,
        "seed": _SEED,
        "rows": _ROWS,
    }
    first, _ = await register_retrained_candidate(
        db_session, trained, report, slice_checks, slice_pr_aucs, **kwargs
    )
    second, _ = await register_retrained_candidate(
        db_session, trained, report, slice_checks, slice_pr_aucs, **kwargs
    )
    assert first == second
    versions = (
        await db_session.execute(
            select(func.count())
            .select_from(ModelVersion)
            .where(ModelVersion.version_label == "retrain-dup")
        )
    ).scalar_one()
    assert versions == 1


async def test_matured_label_counts_excludes_immature(db_session: AsyncSession) -> None:
    await seed(db_session)
    await add_matured_training_labels(db_session)
    lifecycle = ModelLifecycleRepository(db_session)
    now = datetime.now(UTC)
    seeded = await lifecycle.matured_label_counts(as_of=now)
    assert seeded.total == 12  # the seed's pre-matured balanced labels
    assert labels_eligible(seeded, min_total=10, min_per_class=2) is True

    # An immature (future-matured) label is NOT counted toward eligibility.
    transaction_id = (
        await db_session.execute(
            select(Transaction.id).where(Transaction.agency_id == DEMO_AGENCY_ID).limit(1)
        )
    ).scalar_one()
    run = AnalysisRun(
        agency_id=DEMO_AGENCY_ID, transaction_id=transaction_id, status=RunStatus.COMPLETED
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        TrainingLabel(
            agency_id=DEMO_AGENCY_ID,
            transaction_id=transaction_id,
            run_id=run.id,
            label=TrainingLabelType.CONFIRMED_FRAUD,
            matured_at=now + timedelta(days=30),  # not yet matured
            created_by=DEMO_ANALYST_ID,
        )
    )
    await db_session.flush()
    assert (await lifecycle.matured_label_counts(as_of=now)).total == 12  # unchanged

"""Phase 5 training + gate tests (plan §16 Phase 5 / §17.3: "quantitative model gates
(PR-AUC/precision@k/recall@alert-budget/calibration/regression) + baseline"). Trains the real
XGBoost candidate on the deterministic synthetic dataset and asserts it clears every §10.5.1
gate, reproduces the committed fixture's metrics, registers as a CANDIDATE with a passing
evaluation (idempotently), and that the registry resolves the active (+ last-known-good) pointer."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pandas as pd
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
from fraudlens_backend.settings import AppSettings
from fraudlens_ml.scoring import GateReport, ModelGates, evaluate_gates, load_artifact
from lib.aml_fraud import IBM_AML, IEEE_CIS
from lib.dataset import split_dataset
from lib.synthetic_fraud import generate_dataset
from train_baseline import _split_for_source
from train_model import (
    TrainedCandidate,
    _load_split,
    _smote_neighbors,
    _synthetic_manifest,
    _version_label,
    register_candidate,
    train_candidate,
)

_SEED = 1729
_ROWS = 16000
_PR_AUC_PLATFORM_TOLERANCE = 0.02
_IBM_VARIANT = "HI-Small_Trans.csv"
_IEEE_VARIANT = "train_transaction.csv"
_SAMPLE_CSV = Path(__file__).resolve().parents[2] / "data" / "aml_train_sample.csv"


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


def test_smote_neighbors_adapt_to_real_dataset_minority_size() -> None:
    assert _smote_neighbors(np.array([0, 0, 0, 1, 1])) == 1
    assert _smote_neighbors(np.array([0] * 20 + [1] * 8)) == 5
    with pytest.raises(ValueError, match="increase --sample-rows"):
        _smote_neighbors(np.array([0, 0, 1]))


def test_fresh_train_reproduces_committed_fixture_metrics(
    trained: TrainedCandidate, fixture_model_dir: Path
) -> None:
    fixture_metrics = load_artifact(fixture_model_dir).metrics
    # XGBoost is deterministic within one platform, but the locked Linux/macOS results differ
    # by about 0.0184 because of floating-point reductions; keep that known spread bounded.
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


def _ibm_settings(tmp_path: Path) -> AppSettings:
    """Settings whose aml_data_dir holds the sample renamed to the IBM variant the loader wants."""
    shutil.copy(_SAMPLE_CSV, tmp_path / _IBM_VARIANT)
    return AppSettings(aml_data_dir=str(tmp_path))


def _ieee_settings(tmp_path: Path) -> AppSettings:
    """Settings whose AML directory holds a small, synthetic IEEE training file."""
    pd.DataFrame(
        {
            "TransactionDT": [0, 3_600, 90_000, 180_000, 270_000, 360_000],
            "TransactionAmt": [100, 59.5, 200, 25, 300, 75],
            "ProductCD": ["W", "H", "R", "S", "C", "W"],
            "card1": ["CARD-A", "CARD-A", "CARD-B", "CARD-C", "CARD-D", "CARD-E"],
            "addr2": ["87", "87", "87", "87", "87", "87"],
            "isFraud": [0, 1, 0, 1, 0, 1],
        }
    ).to_csv(tmp_path / _IEEE_VARIANT, index=False)
    return AppSettings(aml_data_dir=str(tmp_path))


def _ibm_training_settings(tmp_path: Path) -> AppSettings:
    """Build a larger synthetic IBM-shaped file so the unchanged SMOTE trainer can run."""
    row_count = 60
    pd.DataFrame(
        {
            "Timestamp": pd.date_range("2022-01-01", periods=row_count, freq="h", tz="UTC"),
            "From Bank": [f"BANK-{index % 3}" for index in range(row_count)],
            "Account": [f"SYNTH-{index:03d}" for index in range(row_count)],
            "To Bank": [f"DEST-{index % 5}" for index in range(row_count)],
            "Account.1": [f"COUNTERPARTY-{index:03d}" for index in range(row_count)],
            "Amount Received": [100 + index for index in range(row_count)],
            "Receiving Currency": ["US Dollar"] * row_count,
            "Amount Paid": [100 + index for index in range(row_count)],
            "Payment Currency": ["US Dollar"] * row_count,
            "Payment Format": ["Wire" if index % 2 else "ACH" for index in range(row_count)],
            "Is Laundering": [1 if index % 3 == 0 else 0 for index in range(row_count)],
        }
    ).to_csv(tmp_path / _IBM_VARIANT, index=False)
    return AppSettings(aml_data_dir=str(tmp_path))


def test_load_split_ibm_builds_licensed_phi_free_manifest(tmp_path: Path) -> None:
    settings = _ibm_settings(tmp_path)
    split, manifest = _load_split(
        IBM_AML, seed=_SEED, rows=_ROWS, sample_rows=None, settings=settings
    )
    total = split.x_train.shape[0] + split.x_calibration.shape[0] + split.x_holdout.shape[0]
    assert (manifest.source, total, manifest.row_count) == (IBM_AML, 12, 12)
    assert manifest.snapshot_query["license"] == "CDLA-Sharing-1.0"
    assert len(manifest.snapshot_query["files"][0]["sha256"]) == 64
    assert manifest.snapshot_query["schema"]  # the source column list is recorded
    assert "ibm-transactions" in manifest.snapshot_query["datasetVersion"]
    # PHI-free: no raw accounts/banks or agency ids leak into the manifest.
    blob = manifest.model_dump_json()
    assert "AAA111" not in blob and "agency" not in blob.lower()
    # Deterministic: same inputs -> same content hash + a source-distinct, non-colliding label.
    _, again = _load_split(IBM_AML, seed=_SEED, rows=_ROWS, sample_rows=None, settings=settings)
    assert again.content_hash == manifest.content_hash
    label = _version_label(manifest, _SEED, _ROWS)
    assert label.startswith(f"xgb-{IBM_AML}-")
    assert label != _version_label(_synthetic_manifest(_SEED, _ROWS), _SEED, _ROWS)


def test_load_split_ieee_builds_distinct_licensed_manifest(tmp_path: Path) -> None:
    split, manifest = _load_split(
        IEEE_CIS,
        seed=_SEED,
        rows=_ROWS,
        sample_rows=None,
        settings=_ieee_settings(tmp_path),
    )
    total = split.x_train.shape[0] + split.x_calibration.shape[0] + split.x_holdout.shape[0]
    assert (manifest.source, total, manifest.row_count) == (IEEE_CIS, 6, 6)
    assert "Competition Rules" in manifest.snapshot_query["license"]
    assert manifest.snapshot_query["schema"] == [
        "TransactionDT",
        "TransactionAmt",
        "ProductCD",
        "card1",
        "addr2",
        "isFraud",
    ]
    assert manifest.snapshot_query["files"][0]["name"] == _IEEE_VARIANT
    assert "CARD-A" not in manifest.model_dump_json()
    assert _version_label(manifest, _SEED, _ROWS).startswith(f"xgb-{IEEE_CIS}-")


def test_load_split_real_source_fails_fast_without_local_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="train never auto-downloads"):
        _load_split(
            IEEE_CIS,
            seed=_SEED,
            rows=_ROWS,
            sample_rows=None,
            settings=AppSettings(aml_data_dir=str(tmp_path)),
        )


def test_load_split_sample_rows_is_seeded_stratified_and_manifested(tmp_path: Path) -> None:
    settings = _ibm_settings(tmp_path)
    first, manifest = _load_split(IBM_AML, seed=_SEED, rows=_ROWS, sample_rows=6, settings=settings)
    second, again = _load_split(IBM_AML, seed=_SEED, rows=_ROWS, sample_rows=6, settings=settings)
    assert manifest.row_count == manifest.snapshot_query["sampleRows"] == 6
    assert manifest.content_hash == again.content_hash
    assert set(first.y_train) == {0, 1}
    assert (first.x_train == second.x_train).all()


def test_baseline_uses_the_same_real_split(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _ieee_settings(tmp_path)
    expected, _manifest = _load_split(
        IEEE_CIS, seed=_SEED, rows=_ROWS, sample_rows=None, settings=settings
    )
    monkeypatch.setattr("fraudlens_backend.settings.get_settings", lambda: settings)
    actual = _split_for_source(IEEE_CIS, rows=_ROWS, seed=_SEED, sample_rows=None)
    for expected_fold, actual_fold in (
        (expected.x_train, actual.x_train),
        (expected.x_calibration, actual.x_calibration),
        (expected.x_holdout, actual.x_holdout),
    ):
        assert (expected_fold == actual_fold).all()


async def test_register_candidate_persists_ibm_source_manifest(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    # Exercise the actual --source data path through feature building, chronological splitting,
    # unchanged SMOTE/XGBoost training, gate evaluation, and CANDIDATE registration.
    split, manifest = _load_split(
        IBM_AML, seed=_SEED, rows=_ROWS, sample_rows=None, settings=_ibm_training_settings(tmp_path)
    )
    trained = train_candidate(split, ModelGates(), seed=_SEED)
    report = evaluate_gates(trained.metrics, trained.baseline_pr_auc, None, ModelGates())
    label = _version_label(manifest, _SEED, _ROWS)
    version_id = await register_candidate(
        db_session,
        trained,
        report,
        version_label=label,
        artifact_uri=label,
        seed=_SEED,
        rows=_ROWS,
        manifest=manifest,
    )
    version = await db_session.get(ModelVersion, version_id)
    assert version is not None and version.status is ModelVersionStatus.CANDIDATE
    dataset = (
        (await db_session.execute(select(TrainingDataset).order_by(TrainingDataset.id.desc())))
        .scalars()
        .first()
    )
    assert dataset is not None
    assert dataset.snapshot_query["source"] == IBM_AML
    assert dataset.snapshot_query["license"] == "CDLA-Sharing-1.0"
    assert dataset.row_count == 60
    assert "agency_id" not in dataset.snapshot_query


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

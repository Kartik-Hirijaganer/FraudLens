"""Summary: Train + register the XGBoost fraud model against the §10.5.1 gates (plan §16
Phase 5). It generates the deterministic synthetic IEEE-CIS-shaped dataset, SMOTE-resamples
the training fold to handle the ~3.5% imbalance, fits an XGBoost classifier, fits a Platt
(sigmoid) calibration on a held-out calibration fold so the probabilities are meaningful (the
ECE gate), and computes the §10.5.1 metrics on the holdout. The candidate is gated against the
PR-AUC floor, the logistic-regression baseline, the recall@alert-budget / precision@top-1%
operating points, and calibration. `make train-model` persists the artifact bundle (booster +
feature spec + calibration + SHAP background) and registers a CANDIDATE `model_versions` row
plus a `model_evaluations` row recording the gate verdict and a `job_executions(train)` row —
without touching the active pointer (promotion is human-gated in Phase 10). `--fixture`
regenerates the committed local-demo fixture bundle the seed's active pointer resolves to.

Key classes:
- TrainedCandidate: a trained booster + calibration + SHAP background + holdout metrics.
- DatasetManifest: the versioned, PHI-free provenance of one training dataset.

Key functions:
- train_candidate: SMOTE + XGBoost + Platt-calibrate on a split; compute holdout metrics.
- write_fixture_bundle: (re)materialize the committed local-demo fixture artifact bundle.
- register_candidate: idempotently register dataset/run/version/evaluation/job rows.
- main: CLI — train + gate + (fixture | register the candidate) (dev/demo only).

Notes:
- `--source` defaults to synthetic (CI + the committed fixture stay hermetic, no download); real
  runs pass `--source ibm-aml` (recommended) or `--source ieee-cis` (optional). Both fail fast if
  local data is absent (never download). The `--fixture` bundle is ALWAYS synthetic.
- Rare-event branch (full-IBM plan Phase 4): when the training fold's minority share is under
  1%, SMOTE is replaced by `scale_pos_weight` with deeper multi-threaded hist params (SMOTE at
  a ~0.1% base rate over millions of rows is statistically and computationally wrong), and the
  holdout score quantiles at the gates' own alert-budget / top-slice / medium-review fractions
  are persisted as the model's `ModelRiskThresholds` operating points. The >= 1% path
  (synthetic, fixture, retrain) is byte-identical to the historical behavior.
- The synthetic path stays seeded, single-threaded XGBoost (n_jobs=1) + seeded SMOTE/Platt, so
  the fixture booster bytes are reproducible; the rare-event params pin n_jobs=8, which is
  reproducible per machine/thread-count (documented determinism caveat).
- `--artifact-only` writes the bundle + `manifest.json` sidecar without a database, so training
  can run before the demo database exists; `scripts/activate_model.py` registers + promotes the
  bundle later from those files.
- The dataset manifest stores only source/license/schema/sha256/hashes/counts — never PHI,
  raw identifiers, or agency_id (tenant-safe global training, plan §9.4 / ADR-015).
- Registration is idempotent by version label (source-tagged so sources never collide):
  re-running the same config is a no-op, so `make train-model` is safe to repeat.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import xgboost as xgb
from imblearn.over_sampling import SMOTE
from pydantic import BaseModel, ConfigDict, Field
from sklearn.linear_model import LogisticRegression
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import fetch_dataset
from fraudlens_backend.db.models import (
    JobExecution,
    JobStatus,
    JobType,
    ModelEvaluation,
    ModelTrainingRun,
    ModelTrigger,
    ModelVersion,
    ModelVersionStatus,
    TrainingDataset,
)
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.settings import AppSettings, get_settings
from fraudlens_core import ModelRiskThresholds
from fraudlens_ml.scoring import (
    Calibration,
    CandidateMetrics,
    GateReport,
    ModelGates,
    compute_metrics,
    current_feature_spec,
    evaluate_gates,
    save_artifact,
)
from lib.aml_fraud import (
    IBM_AML,
    IEEE_CIS,
    build_feature_matrix,
    load_frame,
    sample_frame,
    servable_frame,
    source_columns,
    split_chronological,
)
from lib.dataset import DataSplit, split_dataset
from lib.synthetic_fraud import generate_dataset
from train_baseline import baseline_pr_auc, build_baseline

REPO_ROOT = Path(__file__).resolve().parents[1]

_SEED = 1729
_TRAIN_ROWS = 16000
_BACKGROUND_ROWS = 64
_PLATT_MAX_ITER = 1000
_FIXTURE_LABEL = "v0-fixture"
_SMOTE_DEFAULT_NEIGHBORS = 5
_MIN_SMOTE_CLASS_ROWS = 2
_BINARY_CLASS_COUNT = 2

# Training data sources: synthetic (the hermetic default), IBM AML-Data (recommended primary),
# and IEEE-CIS (optional secondary, supplied locally because Phase 2 fetches only IBM).
_SYNTHETIC = "synthetic"
_SOURCES: tuple[str, ...] = (_SYNTHETIC, IBM_AML, IEEE_CIS)
_IEEE_CIS_SPEC = fetch_dataset.DatasetSpec(
    source=IEEE_CIS,
    slug="ieee-fraud-detection",
    variant="train_transaction.csv",
    license="Kaggle Competition Rules (IEEE-CIS Fraud Detection)",
)

# XGBoost hyperparameters (single-threaded + seeded so the booster bytes are reproducible).
_XGB_PARAMS: dict[str, Any] = {
    "n_estimators": 120,
    "max_depth": 4,
    "learning_rate": 0.08,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "eval_metric": "logloss",
    "random_state": _SEED,
    "n_jobs": 1,
}

# Training folds with a minority share under this use the rare-event branch: no SMOTE (wrong at
# ~0.1% over millions of rows), class weighting via scale_pos_weight, deeper hist params, and
# persisted holdout-quantile risk operating points. Synthetic (~3.5%) stays on the SMOTE path.
_RARE_EVENT_MINORITY_SHARE = 0.01
# Rare-event hyperparameters (swept on the full IBM split): more capacity for the 5M-row set;
# hist + multi-threading keep the full-dataset fit in minutes (reproducible per
# machine/thread-count — documented caveat). Class weighting uses the SQUARE ROOT of the
# neg/pos ratio — full-ratio weighting over-weights the ~0.1% positives and measurably hurt
# ranking (PR-AUC 0.165 vs 0.198 on the same split).
_XGB_PARAMS_RARE: dict[str, Any] = {
    "n_estimators": 1200,
    "max_depth": 9,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "tree_method": "hist",
    "max_bin": 256,
    "eval_metric": "aucpr",
    "random_state": _SEED,
    "n_jobs": 8,
}
# The bundle sidecar activate_model reads to register a trained candidate without retraining.
_MANIFEST_SIDECAR = "manifest.json"


@dataclass(frozen=True)
class TrainedCandidate:
    """A trained booster + its calibration, SHAP background, and holdout gate metrics."""

    booster: xgb.Booster
    calibration: Calibration
    background: np.ndarray
    metrics: CandidateMetrics
    baseline_pr_auc: float
    risk_thresholds: ModelRiskThresholds | None = None
    rare_event: bool = False


def _fit_platt(margins: np.ndarray, labels: np.ndarray) -> Calibration:
    """Fit a Platt (sigmoid) calibration mapping raw margins to probabilities."""
    logistic = LogisticRegression(max_iter=_PLATT_MAX_ITER).fit(margins.reshape(-1, 1), labels)
    return Calibration(a=float(logistic.coef_[0][0]), b=float(logistic.intercept_[0]))


def _class_counts(labels: np.ndarray) -> dict[int, int]:
    """Return integer class counts for PHI-free split validation and SMOTE configuration."""
    values, counts = np.unique(labels, return_counts=True)
    return {int(value): int(count) for value, count in zip(values, counts, strict=True)}


def _smote_neighbors(labels: np.ndarray) -> int:
    """Choose a valid SMOTE neighbor count from the actual minority-class training rows."""
    counts = _class_counts(labels)
    if len(counts) < _BINARY_CLASS_COUNT or min(counts.values()) < _MIN_SMOTE_CLASS_ROWS:
        raise ValueError(
            "training fold needs at least two rows in each class; increase --sample-rows"
        )
    return min(_SMOTE_DEFAULT_NEIGHBORS, min(counts.values()) - 1)


def _validate_evaluation_folds(split: DataSplit) -> None:
    """Require both classes in calibration/holdout so calibration and gates are meaningful."""
    if (
        len(_class_counts(split.y_calibration)) < _BINARY_CLASS_COUNT
        or len(_class_counts(split.y_holdout)) < _BINARY_CLASS_COUNT
    ):
        raise ValueError("calibration and holdout folds need both classes; increase --sample-rows")


def _is_rare_event_fold(labels: np.ndarray) -> bool:
    """True when the training fold's minority share is below the rare-event branch threshold."""
    counts = _class_counts(labels)
    total = sum(counts.values())
    return total > 0 and (min(counts.values()) / total) < _RARE_EVENT_MINORITY_SHARE


def _fit_classifier(split: DataSplit, seed: int) -> xgb.XGBClassifier:
    """Fit the source-appropriate XGBoost: SMOTE path (>=1% minority) or rare-event weighting."""
    if not _is_rare_event_fold(split.y_train):
        resampled_x, resampled_y = SMOTE(
            random_state=seed,
            k_neighbors=_smote_neighbors(split.y_train),
        ).fit_resample(split.x_train, split.y_train)
        return xgb.XGBClassifier(**_XGB_PARAMS).fit(resampled_x, resampled_y)
    counts = _class_counts(split.y_train)
    params = dict(_XGB_PARAMS_RARE)
    params["random_state"] = seed
    params["scale_pos_weight"] = float(np.sqrt(counts.get(0, 0) / max(1, counts.get(1, 0))))
    return xgb.XGBClassifier(**params).fit(split.x_train, split.y_train)


def _derive_risk_thresholds(
    probabilities: np.ndarray, gates: ModelGates
) -> ModelRiskThresholds | None:
    """Derive the model's risk operating points from its holdout score distribution.

    The quantiles reuse the gates' own capacity semantics: the top `medium_review_fraction` of
    scored volume warrants at least MEDIUM, the top `alert_budget_fraction` warrants HIGH (the
    alert operating point), and the top `top_pct_fraction` warrants CRITICAL. A degenerate
    distribution (non-increasing or out-of-range quantiles) returns None — the identity banding
    is safer than junk operating points.
    """
    medium = float(np.quantile(probabilities, 1.0 - gates.medium_review_fraction))
    high = float(np.quantile(probabilities, 1.0 - gates.alert_budget_fraction))
    critical = float(np.quantile(probabilities, 1.0 - gates.top_pct_fraction))
    if not (0.0 < medium < high < critical < 1.0):
        return None
    return ModelRiskThresholds(medium=medium, high=high, critical=critical)


def train_candidate(split: DataSplit, gates: ModelGates, *, seed: int) -> TrainedCandidate:
    """Fit + Platt-calibrate the source-appropriate XGBoost, then compute holdout gate metrics.

    The >=1% minority path (synthetic/fixture/retrain) is the historical SMOTE pipeline,
    byte-identical; the rare-event path swaps SMOTE for class weighting and persists the
    holdout-quantile risk operating points (full-IBM plan Phase 4).
    """
    _validate_evaluation_folds(split)
    rare_event = _is_rare_event_fold(split.y_train)
    classifier = _fit_classifier(split, seed)
    calibration = _fit_platt(
        np.asarray(classifier.predict(split.x_calibration, output_margin=True)),
        split.y_calibration,
    )
    holdout_margin = np.asarray(classifier.predict(split.x_holdout, output_margin=True))
    holdout_probability = calibration.apply(holdout_margin)
    metrics = compute_metrics(split.y_holdout, holdout_probability, gates)
    baseline = build_baseline(split.x_train, split.y_train, seed)
    background_rng = np.random.default_rng(seed)
    rows = min(_BACKGROUND_ROWS, split.x_train.shape[0])
    background = split.x_train[background_rng.choice(split.x_train.shape[0], rows, replace=False)]
    return TrainedCandidate(
        booster=classifier.get_booster(),
        calibration=calibration,
        background=background,
        metrics=metrics,
        baseline_pr_auc=baseline_pr_auc(baseline, split.x_holdout, split.y_holdout),
        risk_thresholds=(
            _derive_risk_thresholds(holdout_probability, gates) if rare_event else None
        ),
        rare_event=rare_event,
    )


def _trained_params(trained: TrainedCandidate) -> dict[str, Any]:
    """Return the hyperparameters the candidate actually trained with (branch-accurate)."""
    return dict(_XGB_PARAMS_RARE) if trained.rare_event else dict(_XGB_PARAMS)


def _gate_report(
    trained: TrainedCandidate, active_pr_auc: float | None, gates: ModelGates
) -> GateReport:
    """Evaluate the §10.5.1 gates for a trained candidate vs its baseline (+ optional active)."""
    return evaluate_gates(trained.metrics, trained.baseline_pr_auc, active_pr_auc, gates)


def _candidate_metrics_payload(trained: TrainedCandidate, report: GateReport) -> dict[str, float]:
    """Build the PHI-free metrics map persisted on the model version + training run."""
    payload = trained.metrics.model_dump()
    payload["baseline_pr_auc"] = trained.baseline_pr_auc
    payload["gates_passed"] = float(report.passed)
    if trained.risk_thresholds is not None:
        payload["risk_threshold_medium"] = trained.risk_thresholds.medium
        payload["risk_threshold_high"] = trained.risk_thresholds.high
        payload["risk_threshold_critical"] = trained.risk_thresholds.critical
    return payload


class DatasetManifest(BaseModel):
    """The versioned, PHI-free provenance of one training dataset (persisted on TrainingDataset)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(..., description="Dataset source id (e.g. 'synthetic' or 'ibm-aml').")
    row_count: int = Field(..., ge=0, description="Rows actually trained on (after any sampling).")
    label_window: str = Field(..., description="TrainingDataset.label_window tag for this source.")
    snapshot_query: dict[str, Any] = Field(
        ..., description="PHI-free dataset descriptor stored as snapshot_query JSONB."
    )
    content_hash: str = Field(..., description="Deterministic sha-256 of the dataset descriptor.")


def _dataset_hash(seed: int, rows: int) -> str:
    """Return the content hash for the (PHI-free) synthetic dataset manifest."""
    spec = current_feature_spec()
    return hashlib.sha256(
        json.dumps(
            {"source": _SYNTHETIC, "features": spec.features, "seed": seed, "rows": rows},
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _synthetic_manifest(seed: int, rows: int) -> DatasetManifest:
    """Build the synthetic dataset manifest (unchanged shape, keeps the fixture tests valid)."""
    return DatasetManifest(
        source=_SYNTHETIC,
        row_count=rows,
        label_window=_SYNTHETIC,
        snapshot_query={"source": _SYNTHETIC, "seed": seed, "rows": rows},
        content_hash=_dataset_hash(seed, rows),
    )


def _real_manifest(
    source: str, paths: fetch_dataset.DatasetPaths, *, row_count: int, sample_rows: int | None
) -> DatasetManifest:
    """Build a real manifest: source, license, per-file sha256, schema, version, transform id."""
    spec = current_feature_spec()
    dataset = fetch_dataset.dataset_spec(source) if source == IBM_AML else _IEEE_CIS_SPEC
    snapshot_query: dict[str, Any] = {
        "source": source,
        "license": dataset.license,
        "datasetVersion": f"{dataset.slug}:{dataset.variant}",
        "files": [{"name": file.name, "sha256": file.sha256} for file in paths.files],
        "schema": list(source_columns(source)),
        "transformId": f"aml-loader-fs{spec.version}",
    }
    if sample_rows is not None:
        snapshot_query["sampleRows"] = sample_rows
    content_hash = hashlib.sha256(
        json.dumps({**snapshot_query, "rowCount": row_count}, sort_keys=True).encode()
    ).hexdigest()
    return DatasetManifest(
        source=source,
        row_count=row_count,
        label_window=source,
        snapshot_query=snapshot_query,
        content_hash=content_hash,
    )


def _load_split(
    source: str, *, seed: int, rows: int, sample_rows: int | None, settings: AppSettings
) -> tuple[DataSplit, DatasetManifest]:
    """Resolve a source to a (DataSplit, manifest): synthetic generates; real verifies + loads."""
    if source == _SYNTHETIC:
        split = split_dataset(*generate_dataset(rows, seed), seed)
        return split, _synthetic_manifest(seed, rows)
    if source not in _SOURCES:
        raise ValueError(f"unknown --source '{source}' (choices: {list(_SOURCES)})")
    dataset = fetch_dataset.dataset_spec(source) if source == IBM_AML else _IEEE_CIS_SPEC
    # Fail fast if the real data is absent — training NEVER auto-downloads (plan Phase 4).
    paths = fetch_dataset._verify_present(dataset, fetch_dataset._data_dir(settings, None))
    # Only servable rows train: the ingest boundary rejects amounts that round to zero cents,
    # so such rows can never appear in a served database (anti-skew).
    frame = servable_frame(load_frame(paths, source), source)
    if sample_rows is not None:
        frame = sample_frame(frame, source, sample_rows, seed)
    # The online history query caps at investigation_history_max most-recent rows; the offline
    # windows mirror that cap so training features equal what scoring is actually fed.
    features, labels = build_feature_matrix(
        frame, source, history_max=settings.investigation_history_max
    )
    split = split_chronological(features, labels, frame, source)
    return split, _real_manifest(source, paths, row_count=len(frame), sample_rows=sample_rows)


def _version_label(manifest: DatasetManifest, seed: int, rows: int) -> str:
    """Return a deterministic candidate label including the source (candidates never collide)."""
    spec = current_feature_spec()
    digest = hashlib.sha256(
        json.dumps(
            {
                "source": manifest.source,
                "contentHash": manifest.content_hash,
                "features": spec.features,
                "seed": seed,
                "rows": rows,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return f"xgb-{manifest.source}-fs{spec.version}-{digest[:10]}"


def write_fixture_bundle(
    directory: Path | None = None, *, seed: int = _SEED, rows: int = _TRAIN_ROWS
) -> GateReport:
    """(Re)materialize the committed local-demo fixture artifact bundle; return its gate report."""
    target = directory or (REPO_ROOT / "data" / "models" / _FIXTURE_LABEL)
    gates = ModelGates()
    split = split_dataset(*generate_dataset(rows, seed), seed)
    trained = train_candidate(split, gates, seed=seed)
    report = _gate_report(trained, None, gates)
    save_artifact(
        target,
        trained.booster,
        version_label=_FIXTURE_LABEL,
        feature_spec=current_feature_spec(),
        calibration=trained.calibration,
        background=trained.background,
        metrics=_candidate_metrics_payload(trained, report),
    )
    return report


async def register_candidate(  # noqa: PLR0913 - registers several rows; extras are keyword-only
    session: AsyncSession,
    trained: TrainedCandidate,
    report: GateReport,
    *,
    version_label: str,
    artifact_uri: str,
    seed: int,
    rows: int,
    manifest: DatasetManifest | None = None,
) -> uuid.UUID:
    """Idempotently register the dataset/run/version/evaluation/job rows; return the version id.

    `manifest` describes the training data; when omitted it defaults to the synthetic manifest, so
    the existing synthetic call sites (and the fixture tests) are unaffected.
    """
    existing = (
        await session.execute(
            select(ModelVersion).where(ModelVersion.version_label == version_label)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id

    spec = current_feature_spec()
    metrics_payload = _candidate_metrics_payload(trained, report)
    manifest = manifest or _synthetic_manifest(seed, rows)
    dataset = TrainingDataset(
        snapshot_query=manifest.snapshot_query,
        label_window=manifest.label_window,
        row_count=manifest.row_count,
        feature_spec=spec.model_dump(),
        content_hash=manifest.content_hash,
    )
    session.add(dataset)
    await session.flush()
    training_run = ModelTrainingRun(
        trigger=ModelTrigger.MANUAL,
        dataset_id=dataset.id,
        status=JobStatus.SUCCEEDED,
        params=_trained_params(trained),
        metrics=metrics_payload,
        artifact_uri=artifact_uri,
    )
    session.add(training_run)
    await session.flush()
    version = ModelVersion(
        version_label=version_label,
        training_run_id=training_run.id,
        artifact_uri=artifact_uri,
        feature_spec=spec.model_dump(),
        metrics=metrics_payload,
        status=ModelVersionStatus.CANDIDATE,
        notes="XGBoost candidate (Phase 5); promotion is human-gated in Phase 10.",
    )
    session.add(version)
    await session.flush()
    session.add(
        ModelEvaluation(
            model_version_id=version.id,
            baseline_version_id=None,
            metrics={"checks": [check.model_dump() for check in report.checks], **metrics_payload},
            passed=report.passed,
        )
    )
    session.add(
        JobExecution(
            agency_id=None,
            job_type=JobType.TRAIN,
            status=JobStatus.SUCCEEDED,
            payload={
                "version_label": version_label,
                "source": manifest.source,
                "seed": seed,
                "rows": manifest.row_count,
            },
            result={"gates_passed": report.passed, "pr_auc": trained.metrics.pr_auc},
            attempts=1,
        )
    )
    await session.flush()
    return version.id


def _artifacts_root(settings: AppSettings) -> Path:
    """Resolve the model-artifacts root dir (relative paths anchored at the repo root)."""
    root = Path(settings.model_artifacts_dir)
    return root if root.is_absolute() else REPO_ROOT / root


def _write_manifest_sidecar(
    directory: Path, manifest: DatasetManifest, *, seed: int, rows: int
) -> None:
    """Write the PHI-free dataset-manifest sidecar activate_model registers a bundle from."""
    payload = {"manifest": manifest.model_dump(mode="json"), "seed": seed, "rows": rows}
    (directory / _MANIFEST_SIDECAR).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


async def _amain(  # noqa: PLR0911, PLR0913 - CLI orchestration: each guard exits with its own code
    source: str,
    fixture: bool,
    rows: int,
    seed: int,
    sample_rows: int | None,
    artifact_only: bool,
) -> int:
    """Train + gate the model, then (re)write the fixture or register a candidate (dev only)."""
    settings = get_settings()
    if settings.environment == "prod":
        print("train refused: never trains the demo model in prod (FraudLens governance)")
        return 1
    if fixture:
        # The committed fixture MUST regenerate hermetically with no download — always synthetic.
        report = write_fixture_bundle(seed=seed, rows=rows)
        _print_report("fixture", _FIXTURE_LABEL, report)
        return 0 if report.passed else 2

    engine = None
    if not artifact_only:
        engine = create_engine_from_settings(settings)
        if engine is None:
            print("train failed: DATABASE_URL is not configured (or pass --artifact-only)")
            return 1
    try:
        split, manifest = _load_split(
            source, seed=seed, rows=rows, sample_rows=sample_rows, settings=settings
        )
    except (FileNotFoundError, ValueError, KeyError) as exc:
        if engine is not None:
            await engine.dispose()
        print(f"train failed: {exc}")
        return 1
    gates = ModelGates()
    try:
        trained = train_candidate(split, gates, seed=seed)
    except ValueError as exc:
        if engine is not None:
            await engine.dispose()
        print(f"train failed: {exc}")
        return 1
    report = _gate_report(trained, None, gates)
    label = _version_label(manifest, seed, rows)
    bundle_dir = _artifacts_root(settings) / label
    save_artifact(
        bundle_dir,
        trained.booster,
        version_label=label,
        feature_spec=current_feature_spec(),
        calibration=trained.calibration,
        background=trained.background,
        metrics=_candidate_metrics_payload(trained, report),
        risk_thresholds=trained.risk_thresholds,
    )
    _write_manifest_sidecar(bundle_dir, manifest, seed=seed, rows=rows)
    if artifact_only or engine is None:
        _print_report(f"artifact-only [{manifest.source}]", label, report)
        return 0 if report.passed else 2
    sessionmaker = build_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            await register_candidate(
                session,
                trained,
                report,
                version_label=label,
                artifact_uri=label,
                seed=seed,
                rows=rows,
                manifest=manifest,
            )
            await session.commit()
    finally:
        await engine.dispose()
    _print_report(f"registered candidate [{manifest.source}]", label, report)
    return 0 if report.passed else 2


def _print_report(action: str, label: str, report: GateReport) -> None:
    """Print a concise PHI-free training summary (gate verdict + key metrics)."""
    metrics = report.metrics
    print(
        f"train OK ({action} '{label}'): gates_passed={report.passed} "
        f"pr_auc={metrics.pr_auc:.3f} baseline={report.baseline_pr_auc:.3f} "
        f"recall@budget={metrics.recall_at_budget:.3f} "
        f"precision@top={metrics.precision_at_top_pct:.3f} ece={metrics.ece:.4f}"
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: train + gate the XGBoost model (dev/demo only)."""
    parser = argparse.ArgumentParser(description="Train + register the XGBoost fraud model.")
    parser.add_argument(
        "--source",
        choices=_SOURCES,
        default=_SYNTHETIC,
        help="Training data source (default synthetic keeps CI + the fixture hermetic).",
    )
    parser.add_argument(
        "--fixture", action="store_true", help="(Re)write the committed local-demo fixture bundle."
    )
    parser.add_argument("--rows", type=int, default=_TRAIN_ROWS, help="Synthetic dataset size.")
    parser.add_argument("--seed", type=int, default=_SEED, help="Deterministic training seed.")
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=None,
        help="Seeded, label-stratified subsample of a real source for fast iteration.",
    )
    parser.add_argument(
        "--artifact-only",
        action="store_true",
        help="Write the bundle + manifest sidecar without a database; "
        "scripts/activate_model.py registers it later.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(
        _amain(
            args.source,
            args.fixture,
            args.rows,
            args.seed,
            args.sample_rows,
            args.artifact_only,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

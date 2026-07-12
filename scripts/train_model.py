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
  runs pass `--source ibm-aml`, which fails fast if the fetched data is absent (never downloads).
  The `--fixture` bundle is ALWAYS synthetic so it regenerates with no network.
- Everything is seeded, single-threaded XGBoost (n_jobs=1), and SMOTE/Platt are seeded too, so
  the booster bytes (hence the version label + checksum) are reproducible across runs.
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
    build_feature_matrix,
    load_frame,
    sample_frame,
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

# Training data sources: synthetic (the hermetic default for CI + the committed fixture) and the
# real IBM AML-Data track. IEEE-CIS stays the optional secondary that slots in via the same
# aml_fraud framework later; it is not a fetch/train source yet.
_SYNTHETIC = "synthetic"
_SOURCES: tuple[str, ...] = (_SYNTHETIC, IBM_AML)

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


@dataclass(frozen=True)
class TrainedCandidate:
    """A trained booster + its calibration, SHAP background, and holdout gate metrics."""

    booster: xgb.Booster
    calibration: Calibration
    background: np.ndarray
    metrics: CandidateMetrics
    baseline_pr_auc: float


def _fit_platt(margins: np.ndarray, labels: np.ndarray) -> Calibration:
    """Fit a Platt (sigmoid) calibration mapping raw margins to probabilities."""
    logistic = LogisticRegression(max_iter=_PLATT_MAX_ITER).fit(margins.reshape(-1, 1), labels)
    return Calibration(a=float(logistic.coef_[0][0]), b=float(logistic.intercept_[0]))


def train_candidate(split: DataSplit, gates: ModelGates, *, seed: int) -> TrainedCandidate:
    """SMOTE + XGBoost + Platt-calibrate on a split, then compute the holdout gate metrics."""
    resampled_x, resampled_y = SMOTE(random_state=seed).fit_resample(split.x_train, split.y_train)
    classifier = xgb.XGBClassifier(**_XGB_PARAMS).fit(resampled_x, resampled_y)
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
    )


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
    dataset = fetch_dataset.dataset_spec(source)
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
    dataset = fetch_dataset.dataset_spec(source)
    # Fail fast if the real data is absent — training NEVER auto-downloads (plan Phase 4).
    paths = fetch_dataset._verify_present(dataset, fetch_dataset._data_dir(settings, None))
    frame = load_frame(paths, source)
    if sample_rows is not None:
        frame = sample_frame(frame, source, sample_rows, seed)
    features, labels = build_feature_matrix(frame, source)
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
        params=dict(_XGB_PARAMS),
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


async def _amain(source: str, fixture: bool, rows: int, seed: int, sample_rows: int | None) -> int:
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

    engine = create_engine_from_settings(settings)
    if engine is None:
        print("train failed: DATABASE_URL is not configured")
        return 1
    try:
        split, manifest = _load_split(
            source, seed=seed, rows=rows, sample_rows=sample_rows, settings=settings
        )
    except (FileNotFoundError, ValueError, KeyError) as exc:
        await engine.dispose()
        print(f"train failed: {exc}")
        return 1
    gates = ModelGates()
    trained = train_candidate(split, gates, seed=seed)
    report = _gate_report(trained, None, gates)
    label = _version_label(manifest, seed, rows)
    save_artifact(
        _artifacts_root(settings) / label,
        trained.booster,
        version_label=label,
        feature_spec=current_feature_spec(),
        calibration=trained.calibration,
        background=trained.background,
        metrics=_candidate_metrics_payload(trained, report),
    )
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
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args.source, args.fixture, args.rows, args.seed, args.sample_rows))


if __name__ == "__main__":
    raise SystemExit(main())

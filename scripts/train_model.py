"""Summary: Stable training CLI facade, artifact writer, and candidate-registration workflow.

Key classes:
- (none)

Key functions:
- write_fixture_bundle: materialize the committed demo model bundle.
- register_candidate: idempotently persist candidate provenance and evaluation.
- artifacts_root: resolve the configured model bundle directory.
- main: execute the behavior-compatible training CLI.

Notes:
- Training helpers are re-exported to preserve established imports and test seams.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    JobExecution,
    JobStatus,
    JobType,
    ModelEvaluation,
    ModelTrigger,
    ModelVersion,
    TrainingDataset,
)
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.settings import AppSettings, get_settings
from fraudlens_ml.scoring import GateReport, ModelGates, current_feature_spec, save_artifact
from lib.dataset import split_dataset
from lib.model_datasets import (
    SOURCES,
    SYNTHETIC,
    DatasetManifest,
    load_split,
    synthetic_manifest,
    version_label,
)
from lib.model_training import (
    TrainedCandidate,
    build_candidate_version,
    build_training_run,
    candidate_metrics_payload,
    gate_report,
    smote_neighbors,
    train_candidate,
    trained_params,
)
from lib.synthetic_fraud import generate_dataset

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED = 1729
TRAIN_ROWS = 16000
FIXTURE_LABEL = "v0-fixture"
MANIFEST_SIDECAR = "manifest.json"

_smote_neighbors = smote_neighbors
_synthetic_manifest = synthetic_manifest
_load_split = load_split
_version_label = version_label

__all__ = [
    "FIXTURE_LABEL",
    "MANIFEST_SIDECAR",
    "DatasetManifest",
    "TrainedCandidate",
    "_load_split",
    "_smote_neighbors",
    "_synthetic_manifest",
    "_version_label",
    "artifacts_root",
    "main",
    "register_candidate",
    "train_candidate",
    "write_fixture_bundle",
]


def write_fixture_bundle(
    directory: Path | None = None, *, seed: int = SEED, rows: int = TRAIN_ROWS
) -> GateReport:
    """(Re)materialize the committed local-demo fixture artifact bundle; return its gate report."""
    target = directory or (REPO_ROOT / "data" / "models" / FIXTURE_LABEL)
    gates = ModelGates()
    split = split_dataset(*generate_dataset(rows, seed), seed)
    trained = train_candidate(split, gates, seed=seed)
    report = gate_report(trained, None, gates)
    save_artifact(
        target,
        trained.booster,
        version_label=FIXTURE_LABEL,
        feature_spec=current_feature_spec(),
        calibration=trained.calibration,
        background=trained.background,
        metrics=candidate_metrics_payload(trained, report),
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
    metrics_payload = candidate_metrics_payload(trained, report)
    manifest = manifest or synthetic_manifest(seed, rows)
    dataset = TrainingDataset(
        snapshot_query=manifest.snapshot_query,
        label_window=manifest.label_window,
        row_count=manifest.row_count,
        feature_spec=spec.model_dump(),
        content_hash=manifest.content_hash,
    )
    session.add(dataset)
    await session.flush()
    training_run = build_training_run(
        trigger=ModelTrigger.MANUAL,
        dataset_id=dataset.id,
        params=trained_params(trained),
        metrics=metrics_payload,
        artifact_uri=artifact_uri,
    )
    session.add(training_run)
    await session.flush()
    version = build_candidate_version(
        version_label=version_label,
        training_run_id=training_run.id,
        artifact_uri=artifact_uri,
        feature_spec=spec.model_dump(),
        metrics=metrics_payload,
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


def artifacts_root(settings: AppSettings) -> Path:
    """Resolve the model-artifacts root dir (relative paths anchored at the repo root)."""
    root = Path(settings.model_artifacts_dir)
    return root if root.is_absolute() else REPO_ROOT / root


def _write_manifest_sidecar(
    directory: Path, manifest: DatasetManifest, *, seed: int, rows: int
) -> None:
    """Write the PHI-free dataset-manifest sidecar activate_model registers a bundle from."""
    payload = {"manifest": manifest.model_dump(mode="json"), "seed": seed, "rows": rows}
    (directory / MANIFEST_SIDECAR).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


async def _amain(  # noqa: PLR0911, PLR0913, PLR0917 - CLI orchestration: each guard exits with its own code
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
        _print_report("fixture", FIXTURE_LABEL, report)
        return 0 if report.passed else 2

    engine = None
    if not artifact_only:
        engine = create_engine_from_settings(settings)
        if engine is None:
            print("train failed: DATABASE_URL is not configured (or pass --artifact-only)")
            return 1
    try:
        split, manifest = load_split(
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
    report = gate_report(trained, None, gates)
    label = version_label(manifest, seed, rows)
    bundle_dir = artifacts_root(settings) / label
    save_artifact(
        bundle_dir,
        trained.booster,
        version_label=label,
        feature_spec=current_feature_spec(),
        calibration=trained.calibration,
        background=trained.background,
        metrics=candidate_metrics_payload(trained, report),
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
        choices=SOURCES,
        default=SYNTHETIC,
        help="Training data source (default synthetic keeps CI + the fixture hermetic).",
    )
    parser.add_argument(
        "--fixture", action="store_true", help="(Re)write the committed local-demo fixture bundle."
    )
    parser.add_argument("--rows", type=int, default=TRAIN_ROWS, help="Synthetic dataset size.")
    parser.add_argument("--seed", type=int, default=SEED, help="Deterministic training seed.")
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

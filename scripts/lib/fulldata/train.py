"""Summary: Streamed XGBoost training, checkpoint resume, calibration, and evaluation.

Key classes:
- (none)

Key functions:
- train_candidate: fit, calibrate, evaluate, baseline, threshold, and save one bundle.
- candidate_result_path: resolve one persisted aggregate candidate result.
- load_candidate_evaluation: load one persisted candidate result.

Notes:
- Early stopping uses tuning only; Platt and thresholds use calibration only; holdout is final.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import duckdb
import numpy as np
import xgboost as xgb
from pydantic import TypeAdapter
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score

import fetch_dataset
from fraudlens_ml.scoring import (
    FEATURE_NAMES,
    ArtifactError,
    compute_metrics,
    current_feature_spec,
    evaluate_gates,
    load_artifact,
)
from fraudlens_ml.scoring.artifacts import ModelArtifactMetadata, save_artifact
from lib.fulldata.checkpoints import fit_booster, fold_matrix
from lib.fulldata.config import FullDataConfig, FullDataDataset
from lib.fulldata.features import load_feature_build
from lib.fulldata.folds import FoldManifest, FoldSlice, folded_path, load_fold_manifest
from lib.fulldata.ingest import load_reconciliation
from lib.fulldata.manifest import CandidateEvaluation, peak_rss_mib
from lib.fulldata.parity import load_parity_result
from lib.model_datasets import DatasetManifest
from lib.model_training import derive_risk_thresholds, fit_platt
from lib.study import atomic_write_model, atomic_write_text, canonical_json

_BINARY_CLASSES = 2


def _registration_manifest(
    config: FullDataConfig,
    dataset: FullDataDataset,
    repo_root: Path,
    fold_manifest: FoldManifest,
) -> DatasetManifest:
    """Build the PHI-free dataset sidecar consumed by local activate_model scans."""
    reconciliation = load_reconciliation(config, dataset, repo_root)
    feature_build = load_feature_build(config, dataset, repo_root)
    registry = fetch_dataset.dataset_spec(dataset.source)
    snapshot_query = {
        "candidate": dataset.candidate,
        "configSha256": config.config_sha256,
        "datasetVersion": f"{registry.slug}:{dataset.file}",
        "featureArtifactSha256": feature_build.parquet_sha256,
        "featureSpecVersion": config.features.spec_version,
        "files": [{"name": dataset.file, "sha256": reconciliation.input_sha256}],
        "license": registry.license,
        "namespaceAccountsBySource": config.namespace_accounts_by_source,
        "thresholdsFrom": config.thresholds_from,
        "rows": {
            "source": reconciliation.source_rows,
            "usable": reconciliation.usable_rows,
            "training": fold_manifest.counts.train.rows,
            "tuning": fold_manifest.counts.tuning.rows,
            "calibration": fold_manifest.counts.calibration.rows,
            "evaluation": fold_manifest.counts.holdout.rows,
        },
        "source": dataset.source,
        "transformId": "fulldata-temporal-fsv2",
    }
    row_count = fold_manifest.counts.train.rows
    content_hash = hashlib.sha256(
        json.dumps(
            {"rowCount": row_count, "snapshotQuery": snapshot_query},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return DatasetManifest(
        source=dataset.source,
        row_count=row_count,
        label_window="temporal-train-3/5",
        snapshot_query=snapshot_query,
        content_hash=content_hash,
    )


def _write_registration_sidecar(bundle: Path, manifest: DatasetManifest, *, seed: int) -> None:
    """Atomically persist activate_model's typed registration sidecar contract."""
    atomic_write_text(
        bundle / "manifest.json",
        canonical_json(
            {
                "manifest": manifest.model_dump(mode="json"),
                "rows": manifest.row_count,
                "seed": seed,
            }
        ),
    )


def _completed_candidate(
    config: FullDataConfig,
    dataset: FullDataDataset,
    repo_root: Path,
    feature_sha256: str,
) -> CandidateEvaluation | None:
    """Return a fully hash-bound completed result so successful reruns stay idempotent."""
    result_path = candidate_result_path(config, dataset, repo_root)
    if not result_path.is_file():
        return None
    try:
        result = load_candidate_evaluation(config, dataset, repo_root)
        bundle = repo_root / config.paths.artifacts_dir / result.model_bundle
        load_artifact(bundle)
        metadata = ModelArtifactMetadata.model_validate_json(
            (bundle / "metadata.json").read_text(encoding="utf-8")
        )
        sidecar = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        snapshot = sidecar["manifest"]["snapshot_query"]
    except (ArtifactError, KeyError, OSError, TypeError, ValueError):
        return None
    if (
        metadata.model_sha256 != result.model_sha256
        or snapshot.get("configSha256") != config.config_sha256
        or snapshot.get("featureArtifactSha256") != feature_sha256
        or snapshot.get("thresholdsFrom") != config.thresholds_from
    ):
        return None
    return result


def _class_weight(fold: FoldSlice) -> float:
    negatives = fold.rows - fold.positives
    if fold.positives <= 0 or negatives <= 0:
        raise ValueError("training fold requires both classes for scale_pos_weight")
    return negatives / fold.positives


def _fold_arrays(path: Path, fold: str) -> tuple[np.ndarray, np.ndarray]:
    columns = ", ".join([*FEATURE_NAMES, "label"])
    with duckdb.connect() as connection:
        frame = connection.execute(
            f"SELECT {columns} FROM read_parquet(?) WHERE fold = ? ORDER BY cohort_key, row_id",
            [str(path), fold],
        ).fetch_df()
    return (
        frame.loc[:, FEATURE_NAMES].to_numpy(dtype=np.float32),
        frame["label"].to_numpy(dtype=np.uint8),
    )


def _baseline_sample(
    path: Path, config: FullDataConfig, train_slice: FoldSlice
) -> tuple[np.ndarray, np.ndarray]:
    cap = min(config.training.baseline_sample_rows, train_slice.rows)
    positive_cap = max(1, round(cap * train_slice.prevalence))
    negative_cap = cap - positive_cap
    columns = ", ".join([*FEATURE_NAMES, "label"])
    frames = []
    with duckdb.connect() as connection:
        for label, limit in ((0, negative_cap), (1, positive_cap)):
            frames.append(
                connection.execute(
                    f"SELECT {columns} FROM read_parquet(?) WHERE fold = 'train' AND label = ? "
                    "ORDER BY hash(row_id, ?) LIMIT ?",
                    [str(path), label, config.training.seed, limit],
                ).fetch_df()
            )
    frame = __import__("pandas").concat(frames, ignore_index=True)
    if len(np.unique(frame["label"])) != _BINARY_CLASSES:
        raise ValueError("baseline sample requires both classes")
    return (
        frame.loc[:, FEATURE_NAMES].to_numpy(dtype=np.float32),
        frame["label"].to_numpy(dtype=np.uint8),
    )


def candidate_result_path(
    config: FullDataConfig, dataset: FullDataDataset, repo_root: Path
) -> Path:
    """Resolve the persisted aggregate result for one candidate."""
    return repo_root / config.paths.work_dir / "results" / f"{dataset.source}.json"


def train_candidate(
    config: FullDataConfig, dataset: FullDataDataset, repo_root: Path
) -> CandidateEvaluation:
    """Train and evaluate one source while enforcing parity and temporal data roles."""
    parity = load_parity_result(config, dataset, repo_root)
    completed = _completed_candidate(config, dataset, repo_root, parity.feature_sha256)
    if completed is not None:
        return completed
    fold_manifest = load_fold_manifest(config, dataset, repo_root)
    path = folded_path(config, dataset, repo_root)
    scale_pos_weight = _class_weight(fold_manifest.counts.train)
    started = time.monotonic()
    dtrain = fold_matrix(config, path, "train")
    dtuning = fold_matrix(config, path, "tuning", ref=dtrain)
    checkpoint_dir = repo_root / config.paths.work_dir / "checkpoints" / dataset.source
    booster = fit_booster(
        config,
        dtrain,
        dtuning,
        checkpoint_dir,
        scale_pos_weight,
        checkpoint_identity=f"{config.config_sha256}:{parity.feature_sha256}",
    )
    calibration_x, calibration_y = _fold_arrays(path, "calibration")
    holdout_x, holdout_y = _fold_arrays(path, "holdout")
    calibration_matrix = xgb.DMatrix(calibration_x, feature_names=list(FEATURE_NAMES))
    calibration_margin = booster.predict(calibration_matrix, output_margin=True)
    calibration = fit_platt(np.asarray(calibration_margin), calibration_y)
    calibration_probabilities = calibration.apply(np.asarray(calibration_margin))
    holdout_matrix = xgb.DMatrix(holdout_x, feature_names=list(FEATURE_NAMES))
    holdout_margin = booster.predict(holdout_matrix, output_margin=True)
    holdout_probabilities = calibration.apply(np.asarray(holdout_margin))
    metrics = compute_metrics(holdout_y, holdout_probabilities, config.gates)
    baseline_x, baseline_y = _baseline_sample(path, config, fold_manifest.counts.train)
    baseline = LogisticRegression(max_iter=1000, random_state=config.training.seed).fit(
        baseline_x, baseline_y
    )
    baseline_pr_auc = float(
        average_precision_score(holdout_y, baseline.predict_proba(holdout_x)[:, 1])
    )
    gates = evaluate_gates(metrics, baseline_pr_auc, None, config.gates)
    risk_thresholds = derive_risk_thresholds(calibration_probabilities, config.gates)
    background = baseline_x[: config.training.shap_background_rows]
    bundle_name = f"xgb-{dataset.source}-{config.config_sha256[:12]}"
    bundle = repo_root / config.paths.artifacts_dir / bundle_name
    metadata = save_artifact(
        bundle,
        booster,
        version_label=bundle_name,
        feature_spec=current_feature_spec(),
        calibration=calibration,
        background=background,
        metrics={
            **metrics.model_dump(),
            "baseline_pr_auc": baseline_pr_auc,
            "gates_passed": float(gates.passed),
        },
        risk_thresholds=risk_thresholds,
        threshold_source=config.thresholds_from,
    )
    registration = _registration_manifest(config, dataset, repo_root, fold_manifest)
    _write_registration_sidecar(bundle, registration, seed=config.training.seed)
    result = CandidateEvaluation(
        candidate=dataset.candidate,
        source=dataset.source,
        feature_spec_version=config.features.spec_version,
        model_bundle=bundle_name,
        model_sha256=metadata.model_sha256,
        completed_trees=booster.num_boosted_rounds(),
        scale_pos_weight=scale_pos_weight,
        calibration_method=calibration.method,
        threshold_source=config.thresholds_from,
        risk_thresholds=risk_thresholds,
        metrics=metrics,
        baseline_pr_auc=baseline_pr_auc,
        gates=gates,
        training_seconds=time.monotonic() - started,
        training_peak_rss_mib=peak_rss_mib(),
    )
    atomic_write_model(candidate_result_path(config, dataset, repo_root), result)
    return result


def load_candidate_evaluation(
    config: FullDataConfig, dataset: FullDataDataset, repo_root: Path
) -> CandidateEvaluation:
    """Load a validated persisted candidate result."""
    adapter = TypeAdapter(CandidateEvaluation)
    return adapter.validate_json(
        candidate_result_path(config, dataset, repo_root).read_text(encoding="utf-8")
    )

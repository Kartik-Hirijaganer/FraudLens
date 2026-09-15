"""Checkpoint, calibration-only threshold, manifest, report, and publication tests."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import xgboost as xgb
from fulldata_fakes import fulldata_config, prepare_fulldata

from activate_model import discover_bundles
from fraudlens_ml.scoring import FEATURE_NAMES, ModelGates, load_artifact
from fraudlens_ml.scoring.artifacts import ModelArtifactMetadata
from lib import model_training
from lib.dataset import DataSplit
from lib.experiments.budget import Projection
from lib.fulldata.checkpoints import (
    TrainingCheckpointState,
    advance_checkpoint_state,
    fit_booster,
    fold_matrix,
)
from lib.fulldata.cost import save_cost_projection
from lib.fulldata.folds import fold_manifest_path, folded_path, load_fold_manifest
from lib.fulldata.publish import publish_report, validate_published_artifacts
from lib.fulldata.report import evaluate_run, run_directory, write_report
from lib.fulldata.train import load_candidate_evaluation, train_candidate
from lib.study import atomic_write_model

_REPORT_GOLDEN = Path(__file__).parents[1] / "fixtures" / "fulldata_report_golden.md"
_MANIFEST_GOLDEN = Path(__file__).parents[1] / "fixtures" / "fulldata_manifest_golden.json"


def _normalized_manifest(manifest: object) -> str:
    """Remove runtime/platform values while retaining the complete manifest contract."""
    payload = manifest.model_dump(mode="json", by_alias=True)  # type: ignore[attr-defined]
    for key in ("runId", "commit", "startedAt", "completedAt"):
        payload[key] = f"<{key.upper()}>"
    for key in ("durationSeconds", "peakRssMib"):
        payload[key] = f"<{key.upper()}>"
    payload["libraryVersions"] = dict.fromkeys(sorted(payload["libraryVersions"]), "<VERSION>")
    payload["vmSku"] = "<HOST>"
    payload["stageDurationsSeconds"] = dict.fromkeys(
        sorted(payload["stageDurationsSeconds"]), "<SECONDS>"
    )
    payload["stagePeakRssMib"] = dict.fromkeys(sorted(payload["stagePeakRssMib"]), "<RSS>")
    for source in payload["sources"]:
        evaluation = source["evaluation"]
        evaluation["modelSha256"] = "<MODEL_SHA>"
        evaluation["trainingSeconds"] = "<SECONDS>"
        evaluation["trainingPeakRssMib"] = "<RSS>"
        evaluation["baselinePrAuc"] = "<BASELINE_PR_AUC>"
        evaluation["metrics"] = dict.fromkeys(sorted(evaluation["metrics"]), "<METRIC>")
        evaluation["riskThresholds"] = dict.fromkeys(
            sorted(evaluation["riskThresholds"]), "<THRESHOLD>"
        )
        evaluation["gates"]["baseline_pr_auc"] = "<BASELINE_PR_AUC>"
        evaluation["gates"]["metrics"] = dict.fromkeys(
            sorted(evaluation["gates"]["metrics"]), "<METRIC>"
        )
        for check in evaluation["gates"]["checks"]:
            check["value"] = "<VALUE>"
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def test_early_stop_patience_survives_checkpoint_boundaries() -> None:
    initial = TrainingCheckpointState(
        checkpoint_identity="test",
        completed_trees=0,
        best_score=None,
        stale_rounds=0,
        early_stopped=False,
    )
    first = advance_checkpoint_state(initial, [0.5, 0.6, 0.59], patience=3)
    assert first.completed_trees == 3
    assert first.stale_rounds == 1
    final = advance_checkpoint_state(first, [0.58, 0.57, 0.9], patience=3)
    assert final.completed_trees == 5
    assert final.best_score == 0.6
    assert final.stale_rounds == 3
    assert final.early_stopped is True


def test_checkpoint_resume_matches_uninterrupted_model(tmp_path: Path) -> None:
    config = fulldata_config(tmp_path, max_trees=8)
    prepare_fulldata(config, tmp_path)
    dataset = config.datasets[0]
    path = folded_path(config, dataset, tmp_path)
    folds = load_fold_manifest(config, dataset, tmp_path)
    weight = (folds.counts.train.rows - folds.counts.train.positives) / folds.counts.train.positives
    train = fold_matrix(config, path, "train")
    tuning = fold_matrix(config, path, "tuning", ref=train)
    uninterrupted = fit_booster(config, train, tuning, tmp_path / "full", weight)
    partial = fit_booster(
        config,
        train,
        tuning,
        tmp_path / "resumed",
        weight,
        stop_after_trees=4,
    )
    assert partial.num_boosted_rounds() == 4
    resumed = fit_booster(config, train, tuning, tmp_path / "resumed", weight)
    calibration_x = np.zeros((5, len(FEATURE_NAMES)), dtype=np.float32)
    matrix = xgb.DMatrix(calibration_x, feature_names=list(FEATURE_NAMES))
    np.testing.assert_allclose(uninterrupted.predict(matrix), resumed.predict(matrix), atol=1e-12)


def test_holdout_only_signal_cannot_move_historical_training_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Classifier:
        def predict(self, values: np.ndarray, *, output_margin: bool) -> np.ndarray:
            assert output_margin is True
            return values[:, 0]

        def get_booster(self) -> SimpleNamespace:
            return SimpleNamespace()

    monkeypatch.setattr(model_training, "_fit_classifier", lambda *_args: Classifier())
    monkeypatch.setattr(model_training, "build_baseline", lambda *_args: object())
    monkeypatch.setattr(model_training, "baseline_pr_auc", lambda *_args: 0.0)
    train_x = np.column_stack((np.linspace(-2, 2, 200), np.zeros(200)))
    train_y = np.zeros(200, dtype=np.uint8)
    train_y[0] = 1
    calibration_x = np.column_stack((np.linspace(-3, 3, 20), np.zeros(20)))
    calibration_y = np.array([0, 1] * 10, dtype=np.uint8)
    holdout_y = np.array([0, 1] * 10, dtype=np.uint8)

    def run(holdout_shift: float) -> object:
        split = DataSplit(
            x_train=train_x,
            y_train=train_y,
            x_calibration=calibration_x,
            y_calibration=calibration_y,
            x_holdout=np.column_stack((np.linspace(-2, 2, 20) + holdout_shift, np.zeros(20))),
            y_holdout=holdout_y,
        )
        return model_training.train_candidate(split, ModelGates(), seed=1729).risk_thresholds

    assert run(0.0) == run(100.0)


def test_training_report_and_publication_are_hash_bound(tmp_path: Path) -> None:
    config = fulldata_config(tmp_path, max_trees=8)
    prepare_fulldata(config, tmp_path)
    dataset = config.datasets[0]
    result = train_candidate(config, dataset, tmp_path)
    checkpoint_dir = tmp_path / config.paths.work_dir / "checkpoints" / dataset.source
    checkpoint_count = len(tuple(checkpoint_dir.glob("*.ubj")))
    assert train_candidate(config, dataset, tmp_path) == result
    assert len(tuple(checkpoint_dir.glob("*.ubj"))) == checkpoint_count
    assert load_candidate_evaluation(config, dataset, tmp_path) == result
    assert result.threshold_source == "calibration"
    bundle = tmp_path / config.paths.artifacts_dir / result.model_bundle
    loaded = load_artifact(bundle)
    assert loaded.threshold_source == "calibration"
    assert loaded.metrics["gates_passed"] == float(result.gates.passed)
    folds = load_fold_manifest(config, dataset, tmp_path)
    sidecar = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert sidecar["rows"] == folds.counts.train.rows
    assert sidecar["manifest"]["row_count"] == folds.counts.train.rows
    assert sidecar["manifest"]["snapshot_query"]["rows"] == {
        "calibration": folds.counts.calibration.rows,
        "evaluation": folds.counts.holdout.rows,
        "source": 2000,
        "training": folds.counts.train.rows,
        "tuning": folds.counts.tuning.rows,
        "usable": 1996,
    }
    save_cost_projection(
        config,
        tmp_path,
        rate_key="test-rate",
        pilot_hours=Decimal("1"),
        pilot_rows=Decimal("1000"),
        target_rows=Decimal("2000"),
        projection=Projection(
            projected_hours=Decimal("2"),
            projected_cost_usd=Decimal("3"),
            measurement_count=1,
        ),
    )
    manifest = evaluate_run(config, tmp_path)
    assert manifest.sources[0].reconciliation.source_rows == 2000
    assert manifest.sources[0].reconciliation.usable_rows == 1996
    assert manifest.sources[0].folds.counts.holdout.rows > 0
    assert manifest.peak_rss_mib == max(manifest.stage_peak_rss_mib.values())
    assert manifest.stage_peak_rss_mib["train:ibm-aml"] == result.training_peak_rss_mib
    assert manifest.cost.projected_usd == 3.0
    evaluation = manifest.sources[0].evaluation
    assert evaluation is not None
    assert evaluation.gates.metrics == evaluation.metrics
    assert evaluation.gates.baseline_pr_auc == evaluation.baseline_pr_auc
    assert {check.name: check.value for check in evaluation.gates.checks} == {
        "pr_auc_floor": evaluation.metrics.pr_auc,
        "beats_baseline": evaluation.metrics.pr_auc - evaluation.baseline_pr_auc,
        "recall_at_budget": evaluation.metrics.recall_at_budget,
        "precision_at_top_pct": evaluation.metrics.precision_at_top_pct,
        "calibration_ece": evaluation.metrics.ece,
    }
    assert _normalized_manifest(manifest) == _MANIFEST_GOLDEN.read_text(encoding="utf-8")
    report = write_report(config, tmp_path)
    markdown = (run_directory(config, tmp_path, manifest.run_id) / "study.md").read_text()
    assert "```mermaid" in markdown
    assert "Threshold source" in markdown
    normalized = (
        markdown.replace(manifest.run_id, "<RUN_ID>")
        .replace(manifest.commit, "<COMMIT>")
        .replace(result.model_sha256, "<MODEL_SHA>")
    )
    normalized = re.sub(
        r"Peak RSS: [0-9.]+ MiB\. Host: `[^`]+`\.",
        "Peak RSS: <RSS> MiB. Host: `<HOST>`.",
        normalized,
    )
    normalized = re.sub(
        r"\| train:ibm-aml \| [0-9.]+ \| [0-9.]+ \|",
        "| train:ibm-aml | <SECONDS> | <RSS> |",
        normalized,
    )
    normalized = re.sub(
        r"Total recorded stage time: [0-9.]+ seconds\.",
        "Total recorded stage time: <SECONDS> seconds.",
        normalized,
    )
    normalized = re.sub(
        r"\| hi-small \| [0-9.]+ \| [0-9.]+ \| [0-9.]+ \|",
        "| hi-small | <PR_AUC> | <BASELINE_PR_AUC> | <RECALL_AT_BUDGET> |",
        normalized,
    )
    normalized = re.sub(r"    bar \[[0-9.]+\]", "    bar [<PR_AUC>]", normalized)
    normalized = re.sub(r"    line \[[0-9.]+\]", "    line [<BASELINE_PR_AUC>]", normalized)
    assert normalized == _REPORT_GOLDEN.read_text(encoding="utf-8")
    published = publish_report(report, config, tmp_path)
    validated = validate_published_artifacts(
        published.report_json_path, published.frontend_json_path, config
    )
    assert validated.run_id == report.run_id
    frontend = json.loads(published.frontend_json_path.read_text())
    assert frontend["reportSha256"] == published.report_sha256
    assert frontend["candidates"][0]["evaluationRows"] > 0
    published.frontend_json_path.write_text(
        published.frontend_json_path.read_text().replace(published.report_sha256, "0" * 64)
    )
    with pytest.raises(ValueError, match="drifted"):
        validate_published_artifacts(
            published.report_json_path, published.frontend_json_path, config
        )


def test_training_refuses_without_fresh_parity_or_both_classes(tmp_path: Path) -> None:
    config = fulldata_config(tmp_path, max_trees=4)
    dataset = config.datasets[0]
    with pytest.raises(FileNotFoundError):
        train_candidate(config, dataset, tmp_path)
    prepare_fulldata(config, tmp_path)
    folds = load_fold_manifest(config, dataset, tmp_path)
    bad = folds.model_copy(
        update={
            "counts": folds.counts.model_copy(
                update={"train": folds.counts.train.model_copy(update={"positives": 0})}
            )
        }
    )
    atomic_write_model(fold_manifest_path(config, dataset, tmp_path), bad)
    with pytest.raises(ValueError, match="both classes"):
        train_candidate(config, dataset, tmp_path)


def test_completed_bundle_matches_laptop_activation_scan_contract(tmp_path: Path) -> None:
    """Prove a gates-passed full-data bundle is discoverable without VM/database credentials."""
    config = fulldata_config(tmp_path, max_trees=4)
    prepare_fulldata(config, tmp_path)
    result = train_candidate(config, config.datasets[0], tmp_path)
    bundle = tmp_path / config.paths.artifacts_dir / result.model_bundle
    metadata_path = bundle / "metadata.json"
    metadata = ModelArtifactMetadata.model_validate_json(metadata_path.read_text(encoding="utf-8"))
    atomic_write_model(
        metadata_path,
        metadata.model_copy(update={"metrics": {**metadata.metrics, "gates_passed": 1.0}}),
    )

    discovered = discover_bundles(bundle.parent, label=result.model_bundle)

    assert len(discovered) == 1
    assert (
        discovered[0].rows
        == load_fold_manifest(config, config.datasets[0], tmp_path).counts.train.rows
    )
    assert discovered[0].manifest["snapshot_query"]["thresholdsFrom"] == "calibration"

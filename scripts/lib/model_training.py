"""Summary: XGBoost fitting, calibration, risk thresholds, and model-gate helpers.

Key classes:
- TrainedCandidate: trained booster, calibration, background, and holdout metrics.

Key functions:
- build_training_run:
- build_candidate_version:
- smote_neighbors: derive a safe minority-class neighbor count.
- fit_platt: fit the shared Platt calibration mapping.
- derive_risk_thresholds: derive operating thresholds from calibration probabilities.
- train_candidate: fit, calibrate, evaluate, and package one candidate.
- trained_params:
- gate_report: evaluate the configured quantitative gates.
- candidate_metrics_payload:

Notes:
- The rare-event and historical SMOTE branches retain their existing behavior.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np
import xgboost as xgb
from imblearn.over_sampling import SMOTE
from sklearn.linear_model import LogisticRegression

from fraudlens_backend.db.models import (
    JobStatus,
    ModelTrainingRun,
    ModelTrigger,
    ModelVersion,
    ModelVersionStatus,
)
from fraudlens_core import ModelRiskThresholds
from fraudlens_ml.scoring import (
    Calibration,
    CandidateMetrics,
    GateReport,
    ModelGates,
    compute_metrics,
    evaluate_gates,
)
from lib.dataset import DataSplit
from train_baseline import baseline_pr_auc, build_baseline

SEED = 1729
BACKGROUND_ROWS = 64
PLATT_MAX_ITER = 1000
SMOTE_DEFAULT_NEIGHBORS = 5
MIN_SMOTE_CLASS_ROWS = 2
BINARY_CLASS_COUNT = 2
XGB_PARAMS: dict[str, Any] = {
    "n_estimators": 120,
    "max_depth": 4,
    "learning_rate": 0.08,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "eval_metric": "logloss",
    "random_state": SEED,
    "n_jobs": 1,
}
RARE_EVENT_MINORITY_SHARE = 0.01
XGB_PARAMS_RARE: dict[str, Any] = {
    "n_estimators": 1200,
    "max_depth": 9,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "tree_method": "hist",
    "max_bin": 256,
    "eval_metric": "aucpr",
    "random_state": SEED,
    "n_jobs": 8,
}


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


def build_training_run(
    *,
    trigger: ModelTrigger,
    dataset_id: uuid.UUID,
    params: dict[str, Any],
    metrics: dict[str, Any],
    artifact_uri: str,
) -> ModelTrainingRun:
    """Build the shared successful training-run row for initial and matured-label training."""
    return ModelTrainingRun(
        trigger=trigger,
        dataset_id=dataset_id,
        status=JobStatus.SUCCEEDED,
        params=params,
        metrics=metrics,
        artifact_uri=artifact_uri,
    )


def build_candidate_version(  # noqa: PLR0913 -- explicit persisted row fields.
    *,
    version_label: str,
    training_run_id: uuid.UUID,
    artifact_uri: str,
    feature_spec: dict[str, Any],
    metrics: dict[str, Any],
    notes: str,
) -> ModelVersion:
    """Build a candidate-only version row without changing a deployment pointer."""
    return ModelVersion(
        version_label=version_label,
        training_run_id=training_run_id,
        artifact_uri=artifact_uri,
        feature_spec=feature_spec,
        metrics=metrics,
        status=ModelVersionStatus.CANDIDATE,
        notes=notes,
    )


def fit_platt(margins: np.ndarray, labels: np.ndarray) -> Calibration:
    """Fit a Platt (sigmoid) calibration mapping raw margins to probabilities."""
    logistic = LogisticRegression(max_iter=PLATT_MAX_ITER).fit(margins.reshape(-1, 1), labels)
    return Calibration(a=float(logistic.coef_[0][0]), b=float(logistic.intercept_[0]))


def _class_counts(labels: np.ndarray) -> dict[int, int]:
    """Return integer class counts for PHI-free split validation and SMOTE configuration."""
    values, counts = np.unique(labels, return_counts=True)
    return {int(value): int(count) for value, count in zip(values, counts, strict=True)}


def smote_neighbors(labels: np.ndarray) -> int:
    """Choose a valid SMOTE neighbor count from the actual minority-class training rows."""
    counts = _class_counts(labels)
    if len(counts) < BINARY_CLASS_COUNT or min(counts.values()) < MIN_SMOTE_CLASS_ROWS:
        raise ValueError(
            "training fold needs at least two rows in each class; increase --sample-rows"
        )
    return min(SMOTE_DEFAULT_NEIGHBORS, min(counts.values()) - 1)


def _validate_evaluation_folds(split: DataSplit) -> None:
    """Require both classes in calibration/holdout so calibration and gates are meaningful."""
    if (
        len(_class_counts(split.y_calibration)) < BINARY_CLASS_COUNT
        or len(_class_counts(split.y_holdout)) < BINARY_CLASS_COUNT
    ):
        raise ValueError("calibration and holdout folds need both classes; increase --sample-rows")


def _is_rare_event_fold(labels: np.ndarray) -> bool:
    """True when the training fold's minority share is below the rare-event branch threshold."""
    counts = _class_counts(labels)
    total = sum(counts.values())
    return total > 0 and (min(counts.values()) / total) < RARE_EVENT_MINORITY_SHARE


def _fit_classifier(split: DataSplit, seed: int) -> xgb.XGBClassifier:
    """Fit the source-appropriate XGBoost: SMOTE path (>=1% minority) or rare-event weighting."""
    if not _is_rare_event_fold(split.y_train):
        resampled_x, resampled_y = SMOTE(
            random_state=seed,
            k_neighbors=smote_neighbors(split.y_train),
        ).fit_resample(split.x_train, split.y_train)
        return xgb.XGBClassifier(**XGB_PARAMS).fit(resampled_x, resampled_y)
    counts = _class_counts(split.y_train)
    params = dict(XGB_PARAMS_RARE)
    params["random_state"] = seed
    params["scale_pos_weight"] = float(np.sqrt(counts.get(0, 0) / max(1, counts.get(1, 0))))
    return xgb.XGBClassifier(**params).fit(split.x_train, split.y_train)


def derive_risk_thresholds(
    probabilities: np.ndarray, gates: ModelGates
) -> ModelRiskThresholds | None:
    """Derive the model's risk operating points from a calibration score distribution.

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
    calibration-quantile risk operating points (ADR-025).
    """
    _validate_evaluation_folds(split)
    rare_event = _is_rare_event_fold(split.y_train)
    classifier = _fit_classifier(split, seed)
    calibration_margin = np.asarray(classifier.predict(split.x_calibration, output_margin=True))
    calibration = fit_platt(calibration_margin, split.y_calibration)
    calibration_probability = calibration.apply(calibration_margin)
    holdout_margin = np.asarray(classifier.predict(split.x_holdout, output_margin=True))
    holdout_probability = calibration.apply(holdout_margin)
    metrics = compute_metrics(split.y_holdout, holdout_probability, gates)
    baseline = build_baseline(split.x_train, split.y_train, seed)
    background_rng = np.random.default_rng(seed)
    rows = min(BACKGROUND_ROWS, split.x_train.shape[0])
    background = split.x_train[background_rng.choice(split.x_train.shape[0], rows, replace=False)]
    return TrainedCandidate(
        booster=classifier.get_booster(),
        calibration=calibration,
        background=background,
        metrics=metrics,
        baseline_pr_auc=baseline_pr_auc(baseline, split.x_holdout, split.y_holdout),
        risk_thresholds=(
            derive_risk_thresholds(calibration_probability, gates) if rare_event else None
        ),
        rare_event=rare_event,
    )


def trained_params(trained: TrainedCandidate) -> dict[str, Any]:
    """Return the hyperparameters the candidate actually trained with (branch-accurate)."""
    return dict(XGB_PARAMS_RARE) if trained.rare_event else dict(XGB_PARAMS)


def gate_report(
    trained: TrainedCandidate, active_pr_auc: float | None, gates: ModelGates
) -> GateReport:
    """Evaluate the §10.5.1 gates for a trained candidate vs its baseline (+ optional active)."""
    return evaluate_gates(trained.metrics, trained.baseline_pr_auc, active_pr_auc, gates)


def candidate_metrics_payload(trained: TrainedCandidate, report: GateReport) -> dict[str, float]:
    """Build the PHI-free metrics map persisted on the model version + training run."""
    payload = trained.metrics.model_dump()
    payload["baseline_pr_auc"] = trained.baseline_pr_auc
    payload["gates_passed"] = float(report.passed)
    if trained.risk_thresholds is not None:
        payload["risk_threshold_medium"] = trained.risk_thresholds.medium
        payload["risk_threshold_high"] = trained.risk_thresholds.high
        payload["risk_threshold_critical"] = trained.risk_thresholds.critical
    return payload

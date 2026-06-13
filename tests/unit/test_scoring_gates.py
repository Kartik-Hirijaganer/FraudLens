"""Phase 5 quantitative-gate tests (plan §10.5.1 / §16 Phase 5). Verify each gate metric
(PR-AUC, recall@budget, precision@top-1%, ECE, Brier) and the evaluate_gates pass/fail logic
including the active-regression gate, the beats-baseline gate, and edge cases (no positives,
empty calibration bins), plus that ModelGates parses the camelCase system_config.modelGates."""

from __future__ import annotations

import numpy as np
import pytest

from fraudlens_ml.scoring import (
    CandidateMetrics,
    ModelGates,
    compute_metrics,
    evaluate_gates,
)
from fraudlens_ml.scoring.gates import (
    average_precision,
    brier_score,
    expected_calibration_error,
    precision_at_top_pct,
    recall_at_budget,
)


def _separable() -> tuple[np.ndarray, np.ndarray]:
    """A perfectly-separable, well-calibrated (labels, scores) pair (all gates should pass)."""
    rng = np.random.default_rng(7)
    labels = np.array([0] * 95 + [1] * 5)
    scores = np.where(labels == 1, rng.uniform(0.9, 0.99, 100), rng.uniform(0.0, 0.05, 100))
    return labels, scores


def test_average_precision_and_zero_positive_guard() -> None:
    labels, scores = _separable()
    assert average_precision(labels, scores) == pytest.approx(1.0, abs=1e-6)
    assert average_precision(np.zeros(10), np.linspace(0, 1, 10)) == 0.0


def test_recall_at_budget_and_precision_at_top_pct() -> None:
    labels, scores = _separable()
    # the 5 positives are the 5 highest scores -> top-5% recall is perfect
    assert recall_at_budget(labels, scores, 0.05) == pytest.approx(1.0)
    assert precision_at_top_pct(labels, scores, 0.05) == pytest.approx(1.0)
    assert recall_at_budget(np.zeros(10), np.linspace(0, 1, 10), 0.1) == 0.0


def test_expected_calibration_error_perfect_and_brier() -> None:
    # probabilities equal to the empirical accuracy in each bin -> ECE ~ 0
    labels = np.array([0, 0, 0, 0, 1, 1, 1, 1])
    perfect = np.array([0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0])
    assert expected_calibration_error(labels, perfect, bins=10) == pytest.approx(0.0, abs=1e-9)
    assert brier_score(labels, perfect) == pytest.approx(0.0, abs=1e-9)
    # a wholly miscalibrated prediction is penalized
    assert expected_calibration_error(labels, 1.0 - perfect, bins=10) == pytest.approx(1.0)


def test_compute_metrics_bundles_all_metrics() -> None:
    labels, scores = _separable()
    metrics = compute_metrics(labels, scores, ModelGates())
    assert metrics.pr_auc == pytest.approx(1.0, abs=1e-6)
    assert metrics.recall_at_budget == pytest.approx(1.0)
    assert metrics.ece < 0.05


def _passing_metrics() -> CandidateMetrics:
    return CandidateMetrics(
        pr_auc=0.62, recall_at_budget=0.70, precision_at_top_pct=0.90, ece=0.01, brier=0.02
    )


def test_evaluate_gates_all_pass_without_active() -> None:
    report = evaluate_gates(
        _passing_metrics(), baseline_pr_auc=0.36, active_pr_auc=None, gates=ModelGates()
    )
    assert report.passed is True
    # no active model -> the regression gate is not present
    assert {c.name for c in report.checks} == {
        "pr_auc_floor",
        "beats_baseline",
        "recall_at_budget",
        "precision_at_top_pct",
        "calibration_ece",
    }


def test_evaluate_gates_includes_active_regression_gate() -> None:
    report = evaluate_gates(_passing_metrics(), 0.36, active_pr_auc=0.60, gates=ModelGates())
    names = {c.name for c in report.checks}
    assert "no_active_regression" in names
    assert report.passed is True  # 0.62 >= 0.60 - 0.02


def test_evaluate_gates_fails_on_regression() -> None:
    report = evaluate_gates(_passing_metrics(), 0.36, active_pr_auc=0.70, gates=ModelGates())
    assert report.passed is False  # 0.62 < 0.70 - 0.02
    regression = next(c for c in report.checks if c.name == "no_active_regression")
    assert regression.passed is False


@pytest.mark.parametrize(
    "metrics",
    [
        CandidateMetrics(
            pr_auc=0.40, recall_at_budget=0.70, precision_at_top_pct=0.90, ece=0.01, brier=0.02
        ),
        CandidateMetrics(
            pr_auc=0.62, recall_at_budget=0.50, precision_at_top_pct=0.90, ece=0.01, brier=0.02
        ),
        CandidateMetrics(
            pr_auc=0.62, recall_at_budget=0.70, precision_at_top_pct=0.10, ece=0.01, brier=0.02
        ),
        CandidateMetrics(
            pr_auc=0.62, recall_at_budget=0.70, precision_at_top_pct=0.90, ece=0.20, brier=0.02
        ),
    ],
)
def test_evaluate_gates_fails_when_any_metric_misses(metrics: CandidateMetrics) -> None:
    assert evaluate_gates(metrics, 0.36, None, ModelGates()).passed is False


def test_evaluate_gates_fails_when_not_beating_baseline() -> None:
    # candidate PR-AUC barely above a strong baseline (< 0.02 margin)
    report = evaluate_gates(
        _passing_metrics(), baseline_pr_auc=0.615, active_pr_auc=None, gates=ModelGates()
    )
    assert report.passed is False
    assert next(c for c in report.checks if c.name == "beats_baseline").passed is False


def test_model_gates_parses_camelcase_system_config() -> None:
    gates = ModelGates.model_validate(
        {"prAucFloor": 0.5, "maxRegression": 0.03, "eceMax": 0.04, "alertBudgetFraction": 0.06}
    )
    assert gates.pr_auc_floor == 0.5
    assert gates.max_regression == 0.03
    assert gates.ece_max == 0.04
    assert gates.alert_budget_fraction == 0.06
    # defaults mirror §10.5.1
    assert ModelGates().pr_auc_floor == 0.45
    assert ModelGates().recall_at_budget == 0.60

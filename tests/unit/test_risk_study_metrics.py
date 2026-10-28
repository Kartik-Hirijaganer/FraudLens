"""Behavioral checks for fixed-workload comparisons and actual-policy normalization."""

from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from fraudlens_core import ModelRiskThresholds, RiskPolicy
from lib.risk_study.metrics import (
    at_budget,
    at_threshold,
    compare_selected,
    daily_metrics,
    policy_scores,
)
from lib.risk_study.models import Cohort, StudyConfig


@pytest.mark.parametrize("weight", [0.0, 0.5, 0.6, 0.7, 0.8, 1.0])
@pytest.mark.parametrize(
    "thresholds", [None, ModelRiskThresholds(medium=0.001, high=0.01, critical=0.05)]
)
def test_vector_matches_actual_policy_at_boundaries_and_random_scores(weight, thresholds):
    rng = np.random.default_rng(1729)
    probabilities = np.r_[0, 0.001, 0.01, 0.05, 1, rng.random(1000)]
    rules = rng.random(len(probabilities))
    policy = RiskPolicy(model_weight=weight)
    observed = policy_scores(probabilities, rules, policy, thresholds)
    expected = [
        policy.assess(
            fraud_probability=float(p), rules_subscore=float(r), model_thresholds=thresholds
        ).combined_score
        for p, r in zip(probabilities, rules, strict=True)
    ]
    np.testing.assert_allclose(observed, expected, atol=1e-12, rtol=0)


def test_exact_budget_has_label_independent_ties_and_reports_expected_recall():
    labels, scores = np.array([1, 0, 1, 0, 0]), np.array([1.0, 0.8, 0.8, 0.8, 0.1])
    keys = np.array([0.1, 0.9, 0.2, 0.8, 0.3])
    metric, selected = at_budget(labels, scores, keys, 0.4)
    assert metric.alerts == 2 and metric.true_positives == 2
    assert metric.false_positives == 0
    assert metric.boundary_ties == 3 and metric.selected_ties == 1
    assert metric.expected_recall == pytest.approx(2 / 3)
    _, reversed_labels = at_budget(1 - labels, scores, keys, 0.4)
    np.testing.assert_array_equal(selected, reversed_labels)
    # A fixed cutoff selects all ties; it is not an exact-capacity selector.
    fixed = at_threshold(labels, scores, metric.cutoff)
    assert fixed.alerts == 4 and fixed.precision == 0.5


def test_zero_positive_and_zero_alert_slices_and_ceil_budget():
    y = np.zeros(3, dtype=int)
    metric, _ = at_budget(y, np.array([0.1, 0.2, 0.3]), np.arange(3), 0.05)
    assert metric.alerts == 1 and metric.recall == metric.expected_recall == 0
    empty = at_threshold(y, np.zeros(3), 0.6)
    assert empty.alerts == empty.true_positives == empty.false_positives == 0
    assert empty.precision == 0


@pytest.mark.parametrize("budget", [0, -1, 1.1])
def test_invalid_budget_is_rejected(budget):
    with pytest.raises(ValueError, match="budget evaluation"):
        at_budget(np.array([1]), np.array([0.9]), np.array([0]), budget)
    with pytest.raises(ValueError, match="budget evaluation"):
        at_budget(np.array([]), np.array([]), np.array([]), 0.05)


def test_case_exchanges_and_daily_workloads_are_explicit():
    y = np.array([1, 1, 0, 0])
    baseline, chosen = np.array([1, 0, 1, 0], dtype=bool), np.array([0, 1, 0, 1], dtype=bool)
    assert compare_selected(y, baseline, chosen) == {
        "gained_positives": 1,
        "lost_positives": 1,
        "gained_negatives": 1,
        "lost_negatives": 1,
    }
    days = daily_metrics(
        y, np.array([0.1, 0.8, 0.4, 0.5]), np.arange(4), np.array([0, 1, 86400, 86401]), 0.05
    )
    assert [day["alerts"] for day in days] == [1, 1]
    assert [day["positives"] for day in days] == [2, 0]


def test_cohort_rejects_misaligned_nonbinary_and_nonfinite_data():
    values = {
        "row_id": np.array([1, 2]),
        "label": np.array([0, 1]),
        "probability": np.array([0.1, 0.2]),
        "rules": np.zeros(2),
        "epoch": np.arange(2),
        "holdout": np.array([False, True]),
    }
    assert len(Cohort(**values).label) == 2
    for override in (
        {"row_id": np.array([])},
        {"rules": np.zeros((1, 2))},
        {"label": np.array([0, 2])},
        {"holdout": np.array([0, 2])},
        {"probability": np.array([np.nan, 0.1])},
        {"rules": np.array([0, 1.1])},
    ):
        with pytest.raises(ValidationError):
            Cohort(**(values | override))


@pytest.mark.parametrize("weights", [[0.7], [0.7, 0.7, 1], [0.7, -1, 1], [0.7, 1.1, 1]])
def test_candidate_protocol_requires_baseline_and_model_only(weights):
    with pytest.raises(ValidationError):
        StudyConfig(
            weights=weights,
            budget=0.05,
            seed=1729,
            batch_rows=10,
            policy=RiskPolicy(),
            runs=[],
            output_dir=".local/study",
            replay_source="docs/replay.txt",
            replay_sha256="0" * 64,
        )

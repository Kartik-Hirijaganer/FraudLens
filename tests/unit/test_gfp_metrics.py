"""GFP metric-contract tests (GFP plan Phase 5): the calibration-only minority-F1
threshold (higher threshold on ties, applied once to holdout), the top-k operating
points and prevalence-normalized lift on known answers, and the paired bootstrap —
deterministic per seed, class-stratified replicates, identical resample ids across
compared arms (identical probabilities imply a degenerate zero interval), and the
fixed subset cap."""

from __future__ import annotations

import numpy as np
import pytest

from fraudlens_ml.scoring.gates import ModelGates
from lib.gfp import metrics as metrics_module
from lib.gfp.metrics import (
    BOOTSTRAP_REPLICATES,
    TOP_K_FRACTIONS,
    arm_metrics,
    bootstrap_plan,
    delta_interval,
    f1_at_threshold,
    minority_f1_threshold,
    replicate_pr_aucs,
)

_GATES = ModelGates()


def test_minority_f1_threshold_flags_the_clean_cut() -> None:
    labels = np.array([0, 0, 1, 1])
    probabilities = np.array([0.1, 0.2, 0.8, 0.9])
    threshold = minority_f1_threshold(labels, probabilities)
    assert threshold == pytest.approx(0.8)  # flagging both positives is a perfect F1
    assert f1_at_threshold(labels, probabilities, threshold) == pytest.approx(1.0)


def test_minority_f1_ties_take_the_higher_threshold() -> None:
    # F1 = 2/3 at cuts k=2, k=5, AND k=8 (a three-way tie); the FIRST best cut wins,
    # i.e. the highest threshold (fewest flags — the conservative operating point).
    labels = np.array([1, 1, 0, 0, 1, 0, 0, 1])
    probabilities = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2])
    threshold = minority_f1_threshold(labels, probabilities)
    assert threshold == pytest.approx(0.8)
    assert f1_at_threshold(labels, probabilities, threshold) == pytest.approx(2.0 / 3.0)


def test_minority_f1_requires_a_positive() -> None:
    with pytest.raises(ValueError, match="at least one positive"):
        minority_f1_threshold(np.zeros(4), np.linspace(0, 1, 4))
    assert f1_at_threshold(np.zeros(4), np.linspace(0, 1, 4), 0.5) == 0.0


def test_arm_metrics_known_answers() -> None:
    labels = np.array([1, 0, 0, 0, 0, 0, 0, 0, 0, 1])
    probabilities = np.array([0.95, 0.7, 0.6, 0.5, 0.4, 0.35, 0.3, 0.2, 0.15, 0.9])
    result = arm_metrics(
        dataset_source="ibm-aml",
        arm="B",
        scope="global",
        holdout_labels=labels,
        holdout_probabilities=probabilities,
        calibration_labels=labels,
        calibration_probabilities=probabilities,
        gates=_GATES,
    )
    assert result.holdout.positives == 2
    assert result.holdout.illicit_ratio == pytest.approx(0.2)
    assert result.pr_auc == pytest.approx(1.0)  # both positives outrank every negative
    assert result.pr_auc_normalized == pytest.approx(result.pr_auc / 0.2)
    assert result.roc_auc == pytest.approx(1.0)
    assert [top.fraction for top in result.top_k] == list(TOP_K_FRACTIONS)
    # ceil(0.001 * 10) = 1 row reviewed: the top row is a positive.
    assert result.top_k[0].captured_positives == 1
    assert result.top_k[0].precision == pytest.approx(1.0)
    assert result.top_k[0].recall == pytest.approx(0.5)
    assert result.minority_f1 == pytest.approx(1.0)


def test_arm_metrics_rejects_a_positive_free_holdout() -> None:
    with pytest.raises(ValueError, match="positive rows"):
        arm_metrics(
            dataset_source="ibm-aml",
            arm="B",
            scope="global",
            holdout_labels=np.zeros(6),
            holdout_probabilities=np.linspace(0, 1, 6),
            calibration_labels=np.array([0, 1]),
            calibration_probabilities=np.array([0.1, 0.9]),
            gates=_GATES,
        )


def test_bootstrap_plan_is_deterministic_and_stratified() -> None:
    labels = np.array([1, 0, 0, 1, 0, 0, 0, 1, 0, 0])
    plan_a = bootstrap_plan(labels, seed=1729)
    plan_b = bootstrap_plan(labels, seed=1729)
    assert np.array_equal(plan_a.subset, plan_b.subset)
    assert len(plan_a.replicates) == BOOTSTRAP_REPLICATES
    positives = int(labels.sum())
    for first, second in zip(plan_a.replicates, plan_b.replicates, strict=True):
        assert np.array_equal(first, second)  # same seed -> identical resample ids
        assert int(labels[plan_a.subset][first].sum()) == positives  # class-stratified
    assert not np.array_equal(plan_a.replicates[0], bootstrap_plan(labels, seed=7).replicates[0])


def test_bootstrap_subset_cap_is_stratified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metrics_module, "HOLDOUT_SUBSET_CAP", 10)
    labels = np.array([1] * 5 + [0] * 45)
    plan = bootstrap_plan(labels, seed=1729)
    assert plan.subset.shape[0] == 10
    assert int(labels[plan.subset].sum()) == 1  # round(5 * 10/50) positives retained
    assert np.array_equal(plan.subset, np.sort(plan.subset))


def test_paired_interval_is_zero_for_identical_probabilities() -> None:
    labels = np.array([1, 0, 0, 1, 0, 0, 0, 1, 0, 0])
    probabilities = np.linspace(0.05, 0.95, labels.shape[0])
    plan = bootstrap_plan(labels, seed=1729)
    aucs = replicate_pr_aucs(plan, labels, probabilities)
    interval = delta_interval(aucs, aucs)
    assert interval.lower == interval.upper == 0.0
    assert interval.replicates == BOOTSTRAP_REPLICATES
    better = probabilities + labels  # strictly better ranking of the positives
    improved = replicate_pr_aucs(plan, labels, better)
    upward = delta_interval(aucs, improved)
    assert upward.lower >= 0.0
    assert upward.upper > 0.0
    with pytest.raises(ValueError, match="align"):
        delta_interval(aucs, aucs[:-1])

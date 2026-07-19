"""Summary: The frozen per-arm metric contract + paired bootstrap intervals for the
offline GFP tenant-isolation benchmark (GFP plan Phase 5). One arm x scope x dataset
evaluation produces `ArmMetrics`: raw holdout PR-AUC and its prevalence-normalized
mean-lift (PR-AUC / illicit ratio), ROC-AUC (secondary), Brier + ECE, operating points
at the top 0.1/0.5/1% review budgets, and a minority-class F1 whose threshold is
selected on the CALIBRATION fold only and applied once to holdout. PR-AUC deltas get a
paired 95% interval from 200 deterministic stratified bootstrap replicates drawn over
one fixed <=250k holdout subset — the SAME sampled ids for every compared arm/scope,
so intervals are genuinely paired. PR-AUC / Brier / ECE reuse the served gates module
(`fraudlens_ml.scoring.gates`) — the metric definitions are never re-implemented.

Key classes:
- BootstrapPlan: one dataset's fixed holdout subset + shared replicate index draws.

Key functions:
- minority_f1_threshold: exact best-F1 threshold selected on calibration probabilities.
- f1_at_threshold: minority-class F1 of probabilities at a fixed threshold.
- arm_metrics: assemble one ArmMetrics record on the frozen metric contract.
- bootstrap_plan: build the deterministic stratified subset + replicate draws once.
- replicate_pr_aucs: one arm's PR-AUC per bootstrap replicate (paired via the plan).
- delta_interval: percentile 95% interval of paired replicate PR-AUC deltas.

Notes:
- Review-budget fractions are pinned to the plan contract (0.001 / 0.005 / 0.01); the
  top-k cut flags ceil(fraction * n) rows with a STABLE argsort so ties are ordered
  deterministically.
- On F1 ties the HIGHER threshold wins (fewer flags — the conservative operating
  point); the chosen threshold is a real calibration-fold probability.
- `bootstrap_plan` is built once per dataset and reused for every arm/scope pair, so
  every interval subtracts PR-AUCs computed on identical resampled ids.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from sklearn.metrics import roc_auc_score

from fraudlens_ml.scoring.gates import (
    ModelGates,
    average_precision,
    brier_score,
    expected_calibration_error,
)
from lib.gfp.report import ArmMetrics, HoldoutSummary, PairedDeltaInterval, TopKMetrics

# The plan's review-budget operating points: top 0.1%, 0.5%, and 1% of the holdout.
TOP_K_FRACTIONS: tuple[float, ...] = (0.001, 0.005, 0.01)
# Paired-bootstrap contract pins (plan "Metrics & interpretation").
BOOTSTRAP_REPLICATES = 200
HOLDOUT_SUBSET_CAP = 250_000
_INTERVAL_PERCENTILES = (2.5, 97.5)

Arm = Literal["A", "B", "C"]
Scope = Literal["shared", "global", "per_tenant"]


def minority_f1_threshold(labels: np.ndarray, probabilities: np.ndarray) -> float:
    """Return the exact best-minority-F1 threshold over calibration probabilities.

    Probabilities are sorted descending (stable); every cut k implies flagging the top
    k rows, and F1 is evaluated at each cut. The FIRST best cut wins, i.e. ties take
    the higher threshold. The labels must contain at least one positive.
    """
    labels_arr = np.asarray(labels, dtype=np.float64)
    positives = float(labels_arr.sum())
    if positives == 0:
        raise ValueError("minority-F1 threshold selection needs at least one positive row")
    order = np.argsort(-np.asarray(probabilities), kind="stable")
    sorted_probabilities = np.asarray(probabilities)[order]
    true_positives = np.cumsum(labels_arr[order])
    flagged = np.arange(1, labels_arr.shape[0] + 1, dtype=np.float64)
    precision = true_positives / flagged
    recall = true_positives / positives
    denominator = precision + recall
    f1 = np.where(denominator > 0, 2.0 * precision * recall / np.maximum(denominator, 1e-12), 0.0)
    return float(sorted_probabilities[int(np.argmax(f1))])


def f1_at_threshold(labels: np.ndarray, probabilities: np.ndarray, threshold: float) -> float:
    """Return the minority-class F1 when flagging probabilities >= threshold."""
    labels_arr = np.asarray(labels)
    flagged = np.asarray(probabilities) >= threshold
    true_positives = float(labels_arr[flagged].sum())
    false_positives = float(flagged.sum() - true_positives)
    false_negatives = float(labels_arr.sum() - true_positives)
    denominator = 2.0 * true_positives + false_positives + false_negatives
    if denominator == 0:
        return 0.0
    return 2.0 * true_positives / denominator


def _top_k_metrics(labels: np.ndarray, probabilities: np.ndarray, fraction: float) -> TopKMetrics:
    """Operating point when reviewing the top ceil(fraction * n) rows by score."""
    labels_arr = np.asarray(labels)
    n = labels_arr.shape[0]
    k = max(1, int(np.ceil(fraction * n)))
    flagged = np.argsort(-np.asarray(probabilities), kind="stable")[:k]
    captured = int(labels_arr[flagged].sum())
    positives = int(labels_arr.sum())
    return TopKMetrics(
        fraction=fraction,
        precision=captured / k,
        recall=captured / positives if positives else 0.0,
        captured_positives=captured,
    )


def arm_metrics(  # noqa: PLR0913 - one arm evaluation binds dataset/arm/scope + two folds
    *,
    dataset_source: str,
    arm: Arm,
    scope: Scope,
    holdout_labels: np.ndarray,
    holdout_probabilities: np.ndarray,
    calibration_labels: np.ndarray,
    calibration_probabilities: np.ndarray,
    gates: ModelGates,
) -> ArmMetrics:
    """Assemble one arm x scope x dataset evaluation on the frozen metric contract.

    The minority-F1 threshold is selected on the CALIBRATION fold only and applied
    once to the holdout (never tuned against holdout labels). The holdout must carry
    both classes — the orchestrator validates fold makeup before any training.
    """
    labels_arr = np.asarray(holdout_labels, dtype=np.int64)
    positives = int(labels_arr.sum())
    negatives = int(labels_arr.shape[0] - positives)
    illicit_ratio = positives / labels_arr.shape[0]
    if illicit_ratio <= 0.0:
        raise ValueError("holdout must contain positive rows (validated before training)")
    pr_auc = average_precision(labels_arr, holdout_probabilities)
    threshold = minority_f1_threshold(calibration_labels, calibration_probabilities)
    return ArmMetrics(
        dataset_source=dataset_source,
        arm=arm,
        scope=scope,
        holdout=HoldoutSummary(
            positives=positives, negatives=negatives, illicit_ratio=illicit_ratio
        ),
        pr_auc=pr_auc,
        pr_auc_normalized=pr_auc / illicit_ratio,
        roc_auc=float(roc_auc_score(labels_arr, holdout_probabilities)),
        brier=brier_score(labels_arr, np.asarray(holdout_probabilities)),
        ece=expected_calibration_error(
            labels_arr, np.asarray(holdout_probabilities), gates.calibration_bins
        ),
        top_k=tuple(
            _top_k_metrics(labels_arr, holdout_probabilities, fraction)
            for fraction in TOP_K_FRACTIONS
        ),
        minority_f1=f1_at_threshold(labels_arr, holdout_probabilities, threshold),
        minority_f1_threshold=threshold,
    )


@dataclass(frozen=True)
class BootstrapPlan:
    """One dataset's fixed holdout subset + the shared stratified replicate draws."""

    subset: np.ndarray  # holdout row positions the replicates draw from (fixed per dataset)
    replicates: tuple[np.ndarray, ...]  # positions INTO the subset, one array per replicate


def _stratified_subset(labels: np.ndarray, cap: int, rng: np.random.Generator) -> np.ndarray:
    """Fixed label-stratified subset of at most `cap` rows (all rows when they fit)."""
    n = labels.shape[0]
    if n <= cap:
        return np.arange(n, dtype=np.int64)
    fraction = cap / n
    keep: list[np.ndarray] = []
    for value in np.unique(labels):
        positions = np.flatnonzero(labels == value)
        take = min(positions.shape[0], max(1, round(positions.shape[0] * fraction)))
        keep.append(rng.choice(positions, size=take, replace=False))
    return np.sort(np.concatenate(keep)).astype(np.int64)


def bootstrap_plan(labels: np.ndarray, *, seed: int) -> BootstrapPlan:
    """Build the deterministic subset + replicate draws ONCE per dataset (then reuse).

    Replicates resample WITH replacement within each label class, preserving the
    subset's class composition; every compared arm/scope reuses these exact ids, so
    downstream intervals are paired by construction.
    """
    labels_arr = np.asarray(labels)
    rng = np.random.default_rng(seed)
    subset = _stratified_subset(labels_arr, HOLDOUT_SUBSET_CAP, rng)
    subset_labels = labels_arr[subset]
    class_positions = [np.flatnonzero(subset_labels == value) for value in np.unique(subset_labels)]
    replicates: list[np.ndarray] = []
    for _ in range(BOOTSTRAP_REPLICATES):
        parts = [
            rng.choice(positions, size=positions.shape[0], replace=True)
            for positions in class_positions
        ]
        replicates.append(np.sort(np.concatenate(parts)).astype(np.int64))
    return BootstrapPlan(subset=subset, replicates=tuple(replicates))


def replicate_pr_aucs(
    plan: BootstrapPlan, labels: np.ndarray, probabilities: np.ndarray
) -> np.ndarray:
    """Return one arm's PR-AUC per bootstrap replicate (computed once, reused per pair)."""
    subset_labels = np.asarray(labels)[plan.subset]
    subset_probabilities = np.asarray(probabilities)[plan.subset]
    return np.array(
        [
            average_precision(subset_labels[indices], subset_probabilities[indices])
            for indices in plan.replicates
        ],
        dtype=np.float64,
    )


def delta_interval(from_pr_aucs: np.ndarray, to_pr_aucs: np.ndarray) -> PairedDeltaInterval:
    """Percentile 95% interval of the paired replicate deltas (to minus from)."""
    if from_pr_aucs.shape != to_pr_aucs.shape:
        raise ValueError("paired replicate PR-AUC vectors must align")
    deltas = to_pr_aucs - from_pr_aucs
    lower, upper = np.percentile(deltas, _INTERVAL_PERCENTILES)
    return PairedDeltaInterval(
        lower=float(lower),
        upper=float(upper),
        replicates=int(deltas.shape[0]),
        holdout_subset_cap=HOLDOUT_SUBSET_CAP,
    )

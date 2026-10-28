"""Summary: Actual-policy vector scoring and exact-budget comparison metrics.

Key classes:
- (none)

Key functions:
- policy_scores: interpolate anchors obtained from the actual scalar RiskPolicy.
- selected_metrics:
- at_budget: select exactly ceil(budget*N), resolving ties without labels.
- at_threshold: evaluate a fixed served or calibration-derived cutoff.
- compare_selected: count positive and negative gains/losses against the baseline.
- daily_metrics:

Notes:
- Model probability, policy score, rule subscore and SHAP are different quantities.
- No probability calibration, band threshold or model parameter is fitted here.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from fraudlens_core import ModelRiskThresholds, RiskPolicy
from lib.risk_study.models import RankingMetrics

_PARITY_ROWS = 100


def policy_scores(
    probability: np.ndarray,
    rules: np.ndarray,
    policy: RiskPolicy,
    thresholds: ModelRiskThresholds | None,
) -> np.ndarray:
    """Vectorize the current piecewise-linear policy and assert end-to-end scalar parity."""
    # Serving converts float32 model output to Python float before policy arithmetic.
    clipped = np.clip(np.asarray(probability, dtype=np.float64), 0, 1)
    rules = np.asarray(rules, dtype=np.float64)
    if thresholds is None or policy.model_weight <= 0:
        model_risk = clipped
    else:
        anchors = np.array([0.0, thresholds.medium, thresholds.high, thresholds.critical, 1.0])
        levels = np.array([policy.model_risk(float(p), thresholds) for p in anchors])
        model_risk = np.interp(clipped, anchors, levels)
    scores = np.clip(policy.model_weight * model_risk + (1 - policy.model_weight) * rules, 0, 1)
    for index in np.linspace(0, len(scores) - 1, min(_PARITY_ROWS, len(scores)), dtype=int):
        expected = policy.assess(
            fraud_probability=float(probability[index]),
            rules_subscore=float(rules[index]),
            model_thresholds=thresholds,
        )
        if not np.isclose(scores[index], expected.combined_score, atol=1e-12, rtol=0):
            raise ValueError("vectorized study score disagrees with RiskPolicy.assess")
    return np.asarray(scores)


def selected_metrics(labels: np.ndarray, selected: np.ndarray, cutoff: float) -> RankingMetrics:
    """Compute reconciled counts, including empty alert sets and no-positive slices."""
    alerts, positives = int(selected.sum()), int(labels.sum())
    tp = int(labels[selected].sum())
    recall = tp / positives if positives else 0.0
    return RankingMetrics(
        rows=len(labels),
        positives=positives,
        alerts=alerts,
        true_positives=tp,
        false_positives=alerts - tp,
        recall=recall,
        precision=tp / alerts if alerts else 0.0,
        cutoff=cutoff,
        boundary_ties=0,
        selected_ties=0,
        expected_recall=recall,
    )


def at_budget(
    labels: np.ndarray, scores: np.ndarray, tie_keys: np.ndarray, budget: float
) -> tuple[RankingMetrics, np.ndarray]:
    """Select an identical workload for every candidate, with explicit tie uncertainty."""
    if not len(labels) or not 0 < budget <= 1:
        raise ValueError("budget evaluation requires rows and budget inside (0,1]")
    count = math.ceil(budget * len(labels))
    order = np.lexsort((tie_keys, -scores))
    chosen = np.zeros(len(labels), dtype=bool)
    chosen[order[:count]] = True
    cutoff = float(scores[order[count - 1]])
    above, equal = scores > cutoff, scores == cutoff
    slots = count - int(above.sum())
    result = selected_metrics(labels, chosen, cutoff)
    expected_tp = float(labels[above].sum()) + slots * float(labels[equal].mean())
    return result.model_copy(
        update={
            "boundary_ties": int(equal.sum()),
            "selected_ties": slots,
            "expected_recall": expected_tp / result.positives if result.positives else 0.0,
        }
    ), chosen


def at_threshold(labels: np.ndarray, scores: np.ndarray, cutoff: float) -> RankingMetrics:
    """Count every score >= cutoff; ties may increase workload beyond the nominal budget."""
    return selected_metrics(labels, scores >= cutoff, cutoff)


def compare_selected(
    labels: np.ndarray, baseline: np.ndarray, candidate: np.ndarray
) -> dict[str, int]:
    """Report case exchanges; a net improvement can still lose meaningful positive cases."""
    return {
        "gained_positives": int(labels[candidate & ~baseline].sum()),
        "lost_positives": int(labels[baseline & ~candidate].sum()),
        "gained_negatives": int(((candidate & ~baseline) & (labels == 0)).sum()),
        "lost_negatives": int(((baseline & ~candidate) & (labels == 0)).sum()),
    }


def daily_metrics(
    labels: np.ndarray, scores: np.ndarray, keys: np.ndarray, epochs: np.ndarray, budget: float
) -> list[dict[str, Any]]:
    """Reapply the same review budget per UTC day; sparse slices retain raw counts."""
    days = epochs // (24 * 60 * 60)
    result = []
    for day in np.unique(days):
        subset = days == day
        metric, _ = at_budget(labels[subset], scores[subset], keys[subset], budget)
        result.append({"utc_day": int(day), **metric.model_dump()})
    return result

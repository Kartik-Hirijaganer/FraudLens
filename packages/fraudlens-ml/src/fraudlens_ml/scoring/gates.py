"""Summary: The quantitative model-promotion gates (plan §10.5.1). Promotion is human-gated
AND quantitatively gated so approval is testable, not subjective: a candidate must clear a
PR-AUC floor, not regress materially vs the current active model, beat the logistic-regression
baseline, hit a recall target at the daily alert budget, hold precision in the top slice, and
be well-calibrated (ECE). The thresholds live in `system_config.modelGates` (camelCase JSONB)
and parse straight into `ModelGates` (the defaults here mirror §10.5.1). This module defines
the gates ONCE in `fraudlens-ml` so both Phase 5 training and the Phase 10 retrain/eval use the
identical metric definitions and pass/fail logic (no duplication, rule 5). Pure functions over
holdout label/probability arrays — no DB, no I/O — so they are deterministic and unit-testable.

Key classes:
- ModelGates: the configurable §10.5.1 thresholds (parses system_config.modelGates).
- CandidateMetrics: the metrics computed for one model on a holdout.
- GateCheck: one gate's name, observed value, threshold, and pass/fail.
- GateReport: the overall pass/fail plus every individual GateCheck and the metrics.

Key functions:
- average_precision: PR-AUC (the primary metric at a low base rate).
- recall_at_budget: recall when flagging the top alert-budget fraction by score.
- precision_at_top_pct: precision within the top-fraction highest-scored transactions.
- expected_calibration_error: equal-width-bin ECE so probabilities are meaningful.
- brier_score: the Brier score (tracked alongside ECE).
- compute_metrics: all candidate metrics for one (labels, probabilities) holdout.
- evaluate_gates: apply the gates to candidate vs baseline vs (optional) active metrics.

Notes:
- The active-regression gate is skipped (auto-pass) when there is no current active model,
  so the very first model is judged on the floor + baseline + operating-point gates only.
- recall_at_budget / precision_at_top_pct flag the top ceil(fraction*N) by score; the alert
  budget is a fraction of scored volume (configurable), matching §10.5.1's "alert budget".
- ECE/precision/recall are computed with numpy; PR-AUC and Brier wrap scikit-learn.
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sklearn.metrics import average_precision_score, brier_score_loss

_EPS = 1e-12


class ModelGates(BaseModel):
    """The configurable §10.5.1 promotion thresholds (parses system_config.modelGates)."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, frozen=True, extra="ignore"
    )

    pr_auc_floor: float = Field(default=0.45, description="Minimum acceptable PR-AUC (floor).")
    max_regression: float = Field(
        default=0.02, description="Max PR-AUC drop vs the active model (candidate >= active - x)."
    )
    baseline_margin: float = Field(
        default=0.02, description="PR-AUC margin the candidate must beat the LR baseline by."
    )
    recall_at_budget: float = Field(
        default=0.60, description="Minimum recall when flagging the alert-budget fraction."
    )
    alert_budget_fraction: float = Field(
        default=0.05, description="Fraction of scored volume the daily alert budget allows."
    )
    precision_at_top_pct: float = Field(
        default=0.20, description="Minimum precision within the top-fraction highest scores."
    )
    top_pct_fraction: float = Field(
        default=0.01, description="The top-score fraction for precision@k (e.g. top 1%)."
    )
    ece_max: float = Field(default=0.05, description="Maximum expected calibration error.")
    calibration_bins: int = Field(default=10, ge=2, description="Equal-width bins for ECE.")


class CandidateMetrics(BaseModel):
    """The quantitative metrics computed for one model on a holdout (plan §10.5.1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pr_auc: float = Field(..., description="Precision-recall AUC (primary metric).")
    recall_at_budget: float = Field(..., description="Recall at the alert-budget fraction.")
    precision_at_top_pct: float = Field(..., description="Precision within the top-score slice.")
    ece: float = Field(..., description="Expected calibration error (lower is better).")
    brier: float = Field(..., description="Brier score (tracked alongside ECE).")


class GateCheck(BaseModel):
    """One gate's outcome — its name, observed value, threshold, and pass/fail."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., description="Stable gate identifier.")
    value: float = Field(..., description="The observed metric value.")
    threshold: float = Field(..., description="The threshold the value is checked against.")
    passed: bool = Field(..., description="Whether this individual gate passed.")


class GateReport(BaseModel):
    """The overall promotion verdict: every GateCheck plus the candidate metrics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool = Field(..., description="True only when every gate passed.")
    checks: list[GateCheck] = Field(..., description="Each individual gate's outcome.")
    metrics: CandidateMetrics = Field(..., description="The candidate's computed metrics.")
    baseline_pr_auc: float = Field(..., description="The LR baseline PR-AUC compared against.")
    active_pr_auc: float | None = Field(
        default=None, description="The current active model's PR-AUC (None when none active)."
    )


def average_precision(labels: np.ndarray, probabilities: np.ndarray) -> float:
    """Return the PR-AUC (average precision) of probabilities against binary labels."""
    if labels.sum() == 0:
        return 0.0
    return float(average_precision_score(labels, probabilities))


def _top_k_indices(probabilities: np.ndarray, fraction: float) -> np.ndarray:
    """Return the indices of the top ceil(fraction*N) transactions by descending score."""
    n = probabilities.shape[0]
    k = max(1, int(np.ceil(fraction * n)))
    return np.argsort(-probabilities)[:k]


def recall_at_budget(labels: np.ndarray, probabilities: np.ndarray, fraction: float) -> float:
    """Return recall when flagging the top alert-budget fraction by score."""
    positives = float(labels.sum())
    if positives == 0:
        return 0.0
    flagged = _top_k_indices(probabilities, fraction)
    return float(labels[flagged].sum()) / positives


def precision_at_top_pct(labels: np.ndarray, probabilities: np.ndarray, fraction: float) -> float:
    """Return precision within the top-fraction highest-scored transactions."""
    flagged = _top_k_indices(probabilities, fraction)
    return float(labels[flagged].sum()) / float(flagged.shape[0])


def expected_calibration_error(labels: np.ndarray, probabilities: np.ndarray, bins: int) -> float:
    """Return the equal-width-bin expected calibration error (|confidence - accuracy|)."""
    edges = np.linspace(0.0, 1.0, bins + 1)
    n = probabilities.shape[0]
    total = 0.0
    for index in range(bins):
        lower, upper = edges[index], edges[index + 1]
        in_bin = (
            (probabilities >= lower) & (probabilities <= upper)
            if index == bins - 1
            else (probabilities >= lower) & (probabilities < upper)
        )
        count = int(in_bin.sum())
        if count == 0:
            continue
        confidence = float(probabilities[in_bin].mean())
        accuracy = float(labels[in_bin].mean())
        total += (count / n) * abs(confidence - accuracy)
    return total


def brier_score(labels: np.ndarray, probabilities: np.ndarray) -> float:
    """Return the Brier score of probabilities against binary labels."""
    return float(brier_score_loss(labels, probabilities))


def compute_metrics(
    labels: np.ndarray, probabilities: np.ndarray, gates: ModelGates
) -> CandidateMetrics:
    """Compute every gate metric for one (labels, probabilities) holdout."""
    return CandidateMetrics(
        pr_auc=average_precision(labels, probabilities),
        recall_at_budget=recall_at_budget(labels, probabilities, gates.alert_budget_fraction),
        precision_at_top_pct=precision_at_top_pct(labels, probabilities, gates.top_pct_fraction),
        ece=expected_calibration_error(labels, probabilities, gates.calibration_bins),
        brier=brier_score(labels, probabilities),
    )


def evaluate_gates(
    candidate: CandidateMetrics,
    baseline_pr_auc: float,
    active_pr_auc: float | None,
    gates: ModelGates,
) -> GateReport:
    """Apply the §10.5.1 gates to candidate vs baseline vs (optional) active metrics."""
    checks: list[GateCheck] = [
        GateCheck(
            name="pr_auc_floor",
            value=candidate.pr_auc,
            threshold=gates.pr_auc_floor,
            passed=candidate.pr_auc >= gates.pr_auc_floor,
        ),
        GateCheck(
            name="beats_baseline",
            value=candidate.pr_auc - baseline_pr_auc,
            threshold=gates.baseline_margin,
            passed=candidate.pr_auc - baseline_pr_auc >= gates.baseline_margin - _EPS,
        ),
        GateCheck(
            name="recall_at_budget",
            value=candidate.recall_at_budget,
            threshold=gates.recall_at_budget,
            passed=candidate.recall_at_budget >= gates.recall_at_budget - _EPS,
        ),
        GateCheck(
            name="precision_at_top_pct",
            value=candidate.precision_at_top_pct,
            threshold=gates.precision_at_top_pct,
            passed=candidate.precision_at_top_pct >= gates.precision_at_top_pct - _EPS,
        ),
        GateCheck(
            name="calibration_ece",
            value=candidate.ece,
            threshold=gates.ece_max,
            passed=candidate.ece <= gates.ece_max + _EPS,
        ),
    ]
    if active_pr_auc is not None:
        checks.append(
            GateCheck(
                name="no_active_regression",
                value=candidate.pr_auc - active_pr_auc,
                threshold=-gates.max_regression,
                passed=candidate.pr_auc >= active_pr_auc - gates.max_regression - _EPS,
            )
        )
    return GateReport(
        passed=all(check.passed for check in checks),
        checks=checks,
        metrics=candidate,
        baseline_pr_auc=baseline_pr_auc,
        active_pr_auc=active_pr_auc,
    )

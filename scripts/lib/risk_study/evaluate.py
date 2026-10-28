"""Summary: Calibration-first policy comparison, time slices, thresholds and exchanged cases.

Key classes:
- RunEvidence: public aggregate report with explicit exploratory status.

Key functions:
- evaluate_run: persist the calibration selection before computing final descriptive metrics.

Notes:
- All available periods have historical exposure; no independent trial is claimed.
- Fixed score thresholds and exact review budgets are reported as separate operating modes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from lib.risk_study.inputs import PredictionBinding, load_predictions
from lib.risk_study.metrics import (
    at_budget,
    at_threshold,
    compare_selected,
    daily_metrics,
    policy_scores,
)
from lib.risk_study.models import RankingMetrics, StudyConfig, StudyRun
from lib.risk_study.patterns import positive_patterns, summarize_patterns
from lib.study import atomic_write_model


class RunEvidence(BaseModel):
    """Hash-bound, aggregate-only retrospective comparison."""

    run: StudyRun = Field(..., description="Source, bundle and original split identity.")
    binding: PredictionBinding = Field(..., description="Verified input and cache hashes.")
    model_label: str = Field(..., description="Actual frozen model version.")
    threshold_source: str = Field(..., description="Historical model operating-point provenance.")
    exploratory: bool = Field(
        default=True, description="Results include previously examined periods."
    )
    selected_weight: float = Field(..., description="Candidate chosen using calibration only.")
    calibration: dict[str, RankingMetrics] = Field(
        ..., description="Equal-budget calibration metrics."
    )
    holdout: dict[str, Any] = Field(
        ..., description="Descriptive metrics, thresholds, days and exchanges."
    )


def _name(weight: float) -> str:
    return "model-only" if weight == 1 else f"{weight:.0%}/{1 - weight:.0%}"


def evaluate_run(root: Path, config: StudyConfig, run: StudyRun) -> RunEvidence:
    """Reuse verified probabilities, change policy only, and expose uncertainty and workload."""
    cohort, artifact, binding, _evaluation = load_predictions(root, config, run)
    keys = np.random.default_rng(config.seed).random(len(cohort.label))
    calibration_mask = ~cohort.holdout
    calibration: dict[str, RankingMetrics] = {}
    for weight in config.weights:
        scores = policy_scores(
            cohort.probability[calibration_mask],
            cohort.rules[calibration_mask],
            config.policy.model_copy(update={"model_weight": weight}),
            artifact.risk_thresholds,
        )
        metric, _ = at_budget(
            cohort.label[calibration_mask], scores, keys[calibration_mask], config.budget
        )
        calibration[_name(weight)] = metric
    selected = max(
        config.weights,
        key=lambda w: (calibration[_name(w)].expected_recall, w == config.policy.model_weight),
    )
    # Persist selection before reading holdout labels into any metric calculation.
    selection = RunEvidence(
        run=run,
        binding=binding,
        model_label=artifact.version_label,
        threshold_source=artifact.threshold_source,
        selected_weight=selected,
        calibration=calibration,
        holdout={},
    )
    destination = root / config.output_dir / run.name
    atomic_write_model(destination / "calibration-selection.json", selection)
    print(f"{run.name}: calibration selected {_name(selected)}", flush=True)
    patterns_path = destination / "positive-patterns.json"
    pattern_binding = destination / "positive-patterns.sha256"
    if (
        patterns_path.exists()
        and pattern_binding.exists()
        and pattern_binding.read_text().strip() == binding.cache_sha256
    ):
        patterns = {int(k): tuple(v) for k, v in json.loads(patterns_path.read_text()).items()}
    else:
        patterns = positive_patterns(root, run, cohort)
        patterns_path.write_text(json.dumps(patterns) + "\n", encoding="utf-8")
        pattern_binding.write_text(binding.cache_sha256 + "\n", encoding="utf-8")
    mask = cohort.holdout
    y, p, r, t, epochs = (
        cohort.label[mask],
        cohort.probability[mask],
        cohort.rules[mask],
        keys[mask],
        cohort.epoch[mask],
    )
    baseline_score = policy_scores(p, r, config.policy, artifact.risk_thresholds)
    baseline_model_risk = (
        policy_scores(p, np.zeros_like(r), config.policy, artifact.risk_thresholds)
        / config.policy.model_weight
    )
    _, baseline = at_budget(y, baseline_score, t, config.budget)
    later = epochs > run.training_end_epoch if run.training_end_epoch is not None else None
    holdout: dict[str, Any] = {}
    for weight in config.weights:
        name = _name(weight)
        policy = config.policy.model_copy(update={"model_weight": weight})
        scores = policy_scores(p, r, policy, artifact.risk_thresholds)
        metric, chosen = at_budget(y, scores, t, config.budget)
        cal_cutoff = calibration[name].cutoff
        ids = cohort.row_id[mask]
        gained = ids[(chosen & ~baseline) & (y == 1)]
        lost = ids[(baseline & ~chosen) & (y == 1)]
        holdout[name] = {
            "budget": metric.model_dump(),
            "configured_threshold": at_threshold(y, scores, policy.alert_threshold).model_dump(),
            "calibration_threshold": at_threshold(y, scores, cal_cutoff).model_dump(),
            "calibration_threshold_workload": at_threshold(
                cohort.label[calibration_mask],
                policy_scores(
                    cohort.probability[calibration_mask],
                    cohort.rules[calibration_mask],
                    policy,
                    artifact.risk_thresholds,
                ),
                cal_cutoff,
            ).model_dump(),
            "exchange_against_default": compare_selected(y, baseline, chosen),
            "gained_positive_patterns": summarize_patterns(gained, patterns),
            "lost_positive_patterns": summarize_patterns(lost, patterns),
            "detected_positive_patterns": summarize_patterns(ids[chosen & (y == 1)], patterns),
            "daily_budget": daily_metrics(y, scores, t, epochs, config.budget),
            "fixed_normalization_sensitivity": at_budget(
                y, np.clip(weight * baseline_model_risk + (1 - weight) * r, 0, 1), t, config.budget
            )[0].model_dump(),
        }
        if later is not None and later.any():
            later_metric, later_selected = at_budget(
                y[later], scores[later], t[later], config.budget
            )
            _, later_baseline = at_budget(y[later], baseline_score[later], t[later], config.budget)
            holdout[name]["post_training_period"] = {
                "budget": later_metric.model_dump(),
                "configured_threshold": at_threshold(
                    y[later], scores[later], policy.alert_threshold
                ).model_dump(),
                "exchange_against_default": compare_selected(
                    y[later], later_baseline, later_selected
                ),
                "status": (
                    "retrospective out-of-time slice; already included in aggregate evaluation"
                ),
            }
    raw_metric, raw_selected = at_budget(y, p, t, config.budget)
    normalized = policy_scores(
        p, r, config.policy.model_copy(update={"model_weight": 1.0}), artifact.risk_thresholds
    )
    _, normalized_selected = at_budget(y, normalized, t, config.budget)
    holdout["controls"] = {
        "raw_model": raw_metric.model_dump(),
        "raw_and_normalized_model_only_same_selection": bool(
            np.array_equal(raw_selected, normalized_selected)
        ),
        "all_positive_patterns": summarize_patterns(
            cohort.row_id[mask & (cohort.label == 1)], patterns
        ),
        "time_start": datetime.fromtimestamp(int(epochs.min()), UTC).isoformat(),
        "time_end": datetime.fromtimestamp(int(epochs.max()), UTC).isoformat(),
        "canonical_positive_rule_checks": len(patterns),
        "historical_reference_recall": run.reference_recall,
        "historical_metric_reproduced": (
            bool(np.isclose(raw_metric.recall, run.reference_recall, atol=1e-12, rtol=0))
            if run.reference_recall is not None
            else None
        ),
    }
    report = selection.model_copy(update={"holdout": holdout})
    atomic_write_model(destination / "report.json", report)
    print(
        f"{run.name}: report saved; {len(patterns):,} positive rule contexts verified", flush=True
    )
    return report

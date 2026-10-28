"""Published ranking evidence reconciles counts and preserves the original experiment."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from lib.risk_study.evaluate import RunEvidence
from lib.risk_study.inputs import digest

ROOT = Path(__file__).resolve().parents[2]
BENCHMARKS = ROOT / "docs/reference/benchmarks"


def test_published_counts_budget_and_original_history_reconcile():
    review = json.loads((BENCHMARKS / "risk-ranking-review.json").read_text())
    original = json.loads((BENCHMARKS / "risk-blend-comparison.json").read_text())
    assert (
        digest(BENCHMARKS / "risk-blend-comparison.json")
        == review["verification"]["original_artifact_sha256"]
    )
    assert (
        digest(BENCHMARKS / "risk-blend-comparison.md")
        == review["verification"]["original_report_sha256"]
    )
    assert digest(BENCHMARKS / "risk-blend-comparison-source.py.txt") == original["script_sha256"]
    assert digest(BENCHMARKS / "risk-blend-comparison-checks.py.txt") == original["checks_sha256"]
    for run in review["runs"]:
        RunEvidence.model_validate(run)
        assert run["exploratory"] is True
        for policy in run["holdout"].values():
            if "budget" not in policy:
                continue
            for mode in ("budget", "configured_threshold", "calibration_threshold"):
                x = policy[mode]
                assert x["alerts"] == x["true_positives"] + x["false_positives"]
                assert x["recall"] == pytest.approx(x["true_positives"] / x["positives"])
                assert x["precision"] == pytest.approx(x["true_positives"] / x["alerts"])
            assert policy["budget"]["alerts"] == math.ceil(0.05 * policy["budget"]["rows"])
    study = next(r for r in review["runs"] if r["run"]["name"] == "hi-medium-study")
    for row in original["results"]["holdout"]["policies"]:
        name = f"{row['weight']:.0%}/{1 - row['weight']:.0%}"
        current = study["holdout"][name]["budget"]
        for key in ("recall", "precision", "alerts", "true_positives", "false_positives"):
            assert current[key] == pytest.approx(row[key])


def test_limits_served_identity_and_original_default_are_explicit():
    review = json.loads((BENCHMARKS / "risk-ranking-review.json").read_text())
    assert review["runtime"]["active"][0]["version_label"] == "xgb-ibm-aml-fs2-9d43c5f92a"
    assert review["runtime"]["demo_rule_overrides"] == []
    served = next(r for r in review["runs"] if r["run"]["name"] == "hi-small-served")
    assert served["threshold_source"] == "holdout"
    assert served["holdout"]["controls"]["historical_metric_reproduced"] is False
    assert served["holdout"]["model-only"]["exchange_against_default"]["lost_positives"] == 26
    assert served["holdout"]["model-only"]["exchange_against_default"]["gained_positives"] == 9
    assert all(
        v["maximum_feature_error"] <= v["feature_tolerance"]
        for v in review["verification"]["features"].values()
    )

"""Phase 5 scorer tests (plan §16 Phase 5: "prob in [0,1]"; <1s warm). Verify the scorer
loads the active model via the pointer, returns a calibrated probability in [0,1], ranks a
high-risk transaction above a low-risk one, and rejects a feature-spec it cannot satisfy."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fraudlens_core import RuleContext
from fraudlens_ml.scoring import (
    DeploymentPointer,
    ModelCache,
    Scorer,
    current_feature_spec,
    load_artifact,
    save_artifact,
)
from fraudlens_ml.scoring.features import FeatureSpec

CtxFactory = Callable[..., RuleContext]


def _scorer(fixture_model_dir: Path) -> tuple[Scorer, DeploymentPointer]:
    cache = ModelCache(fixture_model_dir.parent)
    pointer = DeploymentPointer(
        active_version_label="v0-fixture", active_artifact_uri=fixture_model_dir.name
    )
    return Scorer(cache), pointer


def test_score_returns_probability_in_unit_interval(
    fixture_model_dir: Path, make_rule_context: CtxFactory
) -> None:
    scorer, pointer = _scorer(fixture_model_dir)
    out = scorer.score(pointer, make_rule_context(amount="500.00"))
    assert 0.0 <= out.fraud_probability <= 1.0
    assert out.model_version_label == "v0-fixture"


def test_high_risk_scores_above_low_risk(
    fixture_model_dir: Path, make_rule_context: CtxFactory
) -> None:
    scorer, pointer = _scorer(fixture_model_dir)
    high = scorer.score(
        pointer,
        make_rule_context(
            amount="9500.00",
            country="NG",
            channel="wire",
            occurred_at=datetime(2024, 6, 1, 3, 0, tzinfo=UTC),
        ),
    )
    low = scorer.score(
        pointer,
        make_rule_context(
            amount="12.50",
            country="US",
            channel="card",
            occurred_at=datetime(2024, 6, 1, 14, 0, tzinfo=UTC),
        ),
    )
    assert high.fraud_probability > low.fraud_probability


def test_scorer_rejects_unsatisfiable_feature_spec(
    fixture_model_dir: Path, make_rule_context: CtxFactory, tmp_path: Path
) -> None:
    # Save a bundle whose spec demands a feature the extractor cannot supply -> the scorer
    # must refuse rather than score on bad data.
    loaded = load_artifact(fixture_model_dir)
    bogus_spec = FeatureSpec(version=1, features=[*current_feature_spec().features, "ghost"])
    save_artifact(
        tmp_path / "bogus",
        loaded.booster,
        version_label="bogus",
        feature_spec=bogus_spec,
        calibration=loaded.calibration,
        background=loaded.background,
        metrics={},
    )
    pointer = DeploymentPointer(active_version_label="bogus", active_artifact_uri="bogus")
    with pytest.raises(ValueError, match="missing required features"):
        Scorer(ModelCache(tmp_path)).score(pointer, make_rule_context())


def test_loaded_fixture_metrics_round_trip(fixture_model_dir: Path) -> None:
    # the committed fixture records its real holdout metrics in the bundle
    metrics = load_artifact(fixture_model_dir).metrics
    assert metrics["pr_auc"] >= 0.45
    assert metrics["gates_passed"] == 1.0

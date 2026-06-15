"""Phase 5 SHAP-explainer tests (plan §16 Phase 5: "SHAP additive"). Verify the explanation is
exactly additive to the model's raw margin (base_value + sum(shap) == margin), surfaces ranked
top drivers, and reuses a cached TreeExplainer per version (the warm-latency path)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
import xgboost as xgb

from fraudlens_core import RuleContext
from fraudlens_ml.scoring import Explainer, load_artifact
from fraudlens_ml.scoring.features import extract_features, feature_vector

CtxFactory = Callable[..., RuleContext]


def _margin(loaded, ctx: RuleContext) -> float:
    """Compute the raw booster margin for a context (the value SHAP must reconstruct)."""
    vector = feature_vector(extract_features(ctx), loaded.feature_spec.features)
    return float(loaded.booster.predict(xgb.DMatrix(vector), output_margin=True)[0])


def test_shap_explanation_is_additive_to_margin(
    fixture_model_dir: Path, make_rule_context: CtxFactory
) -> None:
    loaded = load_artifact(fixture_model_dir)
    ctx = make_rule_context(
        amount="9500.00",
        country="NG",
        channel="wire",
        occurred_at=datetime(2024, 6, 1, 3, 0, tzinfo=UTC),
    )
    explanation = Explainer().explain(loaded, ctx)
    reconstructed = explanation.base_value + sum(explanation.shap_values.values())
    assert reconstructed == pytest.approx(_margin(loaded, ctx), abs=1e-3)
    assert set(explanation.shap_values) == set(loaded.feature_spec.features)


def test_top_features_are_ranked_and_capped(
    fixture_model_dir: Path, make_rule_context: CtxFactory
) -> None:
    loaded = load_artifact(fixture_model_dir)
    explainer = Explainer(top_k=3)
    explanation = explainer.explain(loaded, make_rule_context(amount="9500.00", country="NG"))
    assert len(explanation.top_features) == 3
    magnitudes = [abs(feature.shap_value) for feature in explanation.top_features]
    assert magnitudes == sorted(magnitudes, reverse=True)  # ranked by |contribution|


def test_explainer_caches_tree_explainer_per_version(
    fixture_model_dir: Path, make_rule_context: CtxFactory
) -> None:
    loaded = load_artifact(fixture_model_dir)
    explainer = Explainer()
    explainer.explain(loaded, make_rule_context())
    explainer.explain(loaded, make_rule_context(amount="50.00"))
    assert list(explainer._explainers) == ["v0-fixture"]  # built once, reused (warm path)

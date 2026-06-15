"""fraudlens-ml exposes its `scoring` package as of Phase 5; importing the top package stays
light (xgboost/shap load only when `fraudlens_ml.scoring` is imported)."""

from __future__ import annotations

import fraudlens_ml
from fraudlens_ml import scoring


def test_ml_top_package_import_is_light() -> None:
    # The heavy scoring API is reached via fraudlens_ml.scoring, not eager top-level exports.
    assert fraudlens_ml.__all__ == []


def test_scoring_package_exposes_phase5_api() -> None:
    for name in ("Scorer", "Explainer", "ModelCache", "ModelGates", "CanaryRouter"):
        assert hasattr(scoring, name)

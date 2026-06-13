"""Summary: SHAP explainability for the fraud scorer (plan §16 Phase 5). `Explainer` wraps a
SHAP `TreeExplainer` over the loaded booster and its persisted interventional background, and
turns one `fraudlens_core.RuleContext` into per-feature contributions plus the top drivers an
analyst sees in the investigation. It explains the model's RAW margin (log-odds), where SHAP
is exactly additive: `base_value + sum(shap_values)` reconstructs the margin (asserted in the
§17 SHAP-additivity test). It caches one TreeExplainer per model version label, so after the
first (cold) build every subsequent explanation is sub-millisecond — keeping the scorer+SHAP
step inside the warm latency budget (plan §16 Phase 5 acceptance: <1s warm). The contributions
carry only feature NAMES + numeric values (no PHI), so they can flow into `analysis_results`
and the SAR rationale without leaking (plan §9.4 tenant-safe artifacts).

Key classes:
- FeatureContribution: one feature's value and its signed SHAP contribution to the margin.
- Explanation: the base value, every feature's SHAP contribution, and the top-k drivers.
- Explainer: builds/caches a TreeExplainer per model version and explains a RuleContext.

Key functions:
- (none)

Notes:
- Calibration is a monotonic post-transform of the margin, so SHAP explains the raw margin
  (the standard, defensible attribution) while the reported probability is calibrated.
- The TreeExplainer cache is keyed by version label; a pointer flip to a new version builds a
  fresh explainer once, and a rollback to a prior version reuses the cached one (warm).
- top_features is ranked by absolute contribution; the full `shap_values` map preserves the
  additive reconstruction regardless of how many drivers are surfaced.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import shap
from pydantic import BaseModel, ConfigDict, Field

from fraudlens_core import RuleContext
from fraudlens_ml.scoring.artifacts import LoadedArtifact
from fraudlens_ml.scoring.features import extract_features, feature_vector

_DEFAULT_TOP_K = 8


class FeatureContribution(BaseModel):
    """One feature's value and its signed SHAP contribution to the raw margin."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    feature: str = Field(..., description="Feature name.")
    value: float = Field(..., description="The feature's value for this transaction.")
    shap_value: float = Field(..., description="Signed contribution to the model margin.")


class Explanation(BaseModel):
    """The SHAP explanation: base value, every contribution, and the top-k drivers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_value: float = Field(..., description="Explainer expected value (margin at baseline).")
    shap_values: dict[str, float] = Field(
        ..., description="Per-feature signed contributions (sum + base == margin)."
    )
    top_features: list[FeatureContribution] = Field(
        ..., description="The highest-|contribution| drivers, most important first."
    )


class Explainer:
    """Builds and caches a SHAP TreeExplainer per model version and explains a RuleContext."""

    def __init__(self, top_k: int = _DEFAULT_TOP_K) -> None:
        """Bind how many top drivers to surface; init the per-version explainer cache."""
        self._top_k = top_k
        self._explainers: dict[str, Any] = {}

    def _explainer_for(self, loaded: LoadedArtifact) -> Any:
        """Return the cached TreeExplainer for a version, building it once on first use."""
        cached = self._explainers.get(loaded.version_label)
        if cached is not None:
            return cached
        built = shap.TreeExplainer(
            loaded.booster,
            data=loaded.background,
            feature_perturbation="interventional",
            model_output="raw",
        )
        self._explainers[loaded.version_label] = built
        return built

    def explain(self, loaded: LoadedArtifact, context: RuleContext) -> Explanation:
        """Explain one transaction's raw margin as additive per-feature SHAP contributions."""
        names = loaded.feature_spec.features
        vector = feature_vector(extract_features(context), names)
        explainer = self._explainer_for(loaded)
        raw = np.asarray(explainer.shap_values(vector)).reshape(-1)
        base_value = float(np.atleast_1d(explainer.expected_value)[0])
        values = {name: float(vector[0][index]) for index, name in enumerate(names)}
        contributions = {name: float(raw[index]) for index, name in enumerate(names)}
        top = sorted(contributions.items(), key=lambda item: abs(item[1]), reverse=True)
        top_features = [
            FeatureContribution(feature=name, value=values[name], shap_value=shap_value)
            for name, shap_value in top[: self._top_k]
        ]
        return Explanation(
            base_value=base_value, shap_values=contributions, top_features=top_features
        )

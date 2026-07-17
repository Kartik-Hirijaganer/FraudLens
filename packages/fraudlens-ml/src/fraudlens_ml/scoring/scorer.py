"""Summary: The fraud scorer (plan §16 Phase 5). `Scorer` turns a PHI-free
`fraudlens_core.RuleContext` into a calibrated fraud probability using the active model the
registry pointer names. It resolves the artifact through `ModelCache` (so it lazily loads the
active version, reloads on a pointer flip, and serves the last-known-good model if the active
artifact is missing/corrupt — plan §10.6), extracts the deterministic feature vector in the
model's own persisted column order, predicts the raw booster margin, and maps it through the
stored Platt calibration to a probability in [0, 1] (the calibration the §10.5.1 ECE gate
guards). It returns the probability plus the version label actually used, so the caller can
tell when a fallback served a different version than the pointer's active one.

Key classes:
- ScoreOutput: the scoring result — calibrated probability + the version label used.
- Scorer: scores a RuleContext via the active (pointer-resolved) model.

Key functions:
- (none)

Notes:
- The feature vector is ordered by the LOADED artifact's `feature_spec`, not the current
  code constant, so an older model is always scored with the exact columns it was trained on;
  a feature the spec names but the extractor can't produce raises (no silent wrong scoring).
- The probability is clamped to [0, 1] defensively even though the calibration sigmoid is
  already bounded, so a downstream banding step never sees an out-of-range value.
- Scoring touches no DB and no network — the pointer + artifacts are supplied by the backend.
"""

from __future__ import annotations

import xgboost as xgb
from pydantic import BaseModel, ConfigDict, Field

from fraudlens_core import ModelRiskThresholds, RuleContext
from fraudlens_ml.scoring.artifacts import DeploymentPointer, ModelCache
from fraudlens_ml.scoring.features import extract_features, feature_vector


class ScoreOutput(BaseModel):
    """The scoring result: the calibrated fraud probability and the model version used."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fraud_probability: float = Field(..., ge=0.0, le=1.0, description="Calibrated P(fraud).")
    model_version_label: str = Field(..., description="Version label of the model that scored.")
    risk_thresholds: ModelRiskThresholds | None = Field(
        default=None,
        description="The scoring model's persisted risk operating points (None on legacy models).",
    )


class Scorer:
    """Scores a RuleContext via the active model named by the registry pointer."""

    def __init__(self, cache: ModelCache) -> None:
        """Bind the artifact cache the scorer resolves the active model through."""
        self._cache = cache

    def score(self, pointer: DeploymentPointer, context: RuleContext) -> ScoreOutput:
        """Score one transaction, returning the calibrated probability + version used."""
        loaded = self._cache.get(pointer)
        vector = feature_vector(extract_features(context), loaded.feature_spec.features)
        margin = loaded.booster.predict(xgb.DMatrix(vector), output_margin=True)
        probability = float(loaded.calibration.apply(margin)[0])
        return ScoreOutput(
            fraud_probability=min(1.0, max(0.0, probability)),
            model_version_label=loaded.version_label,
            risk_thresholds=loaded.risk_thresholds,
        )

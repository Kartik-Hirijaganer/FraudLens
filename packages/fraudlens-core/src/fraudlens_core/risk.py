"""Summary: The deterministic risk-blend that turns the two upstream subscores — the
deterministic rules subscore (`RuleEvaluation.subscore`) and the calibrated model
fraud probability — into the single `combined_score` + `RiskBand` an investigation
persists (plan §10.1 "combined score+band", §16 Phase 8 "risk-blend in core"). It lives
in `fraudlens-core` on purpose: it is pure, framework-free, and shared by the LangGraph
pipeline (`fraudlens-ml`) and the backend without crossing a layering boundary, exactly
like `RiskBand`. `RiskPolicy` is the tunable, PHI-free configuration — the model-vs-rules
blend weight, the cumulative band lower-bounds, and the alert threshold — whose DEFAULTS
match the seeded global `system_config` (`riskBandThresholds` / `alertThreshold`), so the
backend can override them from config but a DB outage still yields a meaningful decision
(the "safe cached in-process defaults", plan §9.1). `assess` is the one entry point: it
blends, bands, and decides whether the run should raise an alert.

Key classes:
- RiskPolicy: the blend weight, cumulative band thresholds, and alert threshold (tunable).
- RiskAssessment: the blended `combined_score`, the resolved `RiskBand`, and the alert flag.

Key functions:
- (none)

Notes:
- The blend is a convex combination `w*model + (1-w)*rules` then clamped to [0, 1], so the
  combined score is always a valid probability-like value for banding (deterministic, no RNG).
- Banding picks the HIGHEST band whose lower bound is <= the score; an empty/garbled threshold
  map falls back to `RiskBand.LOW`, so banding never raises on bad config (graceful degradation).
- `RiskPolicy` defaults are the canonical source the seed mirrors; changing them here changes
  the documented defaults (no duplicated magic thresholds across the codebase, rule 5).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_core.types import RiskBand

# Clamp bounds for the blended score (named so they are not magic literals, rule 4 / PLR2004).
_MIN_SCORE = 0.0
_MAX_SCORE = 1.0

# Documented default blend/banding policy — mirrored by the seeded global `system_config`
# (`riskBandThresholds` / `alertThreshold`); the model-vs-rules weight is a core default
# (no `system_config` key in v1), so a single source of truth lives here (plan §10.1 / §9.1).
_DEFAULT_MODEL_WEIGHT = 0.7
_DEFAULT_BAND_THRESHOLDS: dict[RiskBand, float] = {
    RiskBand.LOW: 0.0,
    RiskBand.MEDIUM: 0.3,
    RiskBand.HIGH: 0.6,
    RiskBand.CRITICAL: 0.85,
}
_DEFAULT_ALERT_THRESHOLD = 0.6


class RiskAssessment(BaseModel):
    """The blended combined score, the resolved risk band, and whether to alert."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    combined_score: float = Field(
        ..., ge=0.0, le=1.0, description="Blended rules+model risk score in [0, 1]."
    )
    risk_band: RiskBand = Field(..., description="Ordinal band resolved from the combined score.")
    alert: bool = Field(..., description="True when the combined score meets the alert threshold.")


class RiskPolicy(BaseModel):
    """The tunable, PHI-free risk-blend policy (defaults mirror the seeded `system_config`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_weight: float = Field(
        default=_DEFAULT_MODEL_WEIGHT,
        ge=0.0,
        le=1.0,
        description="Weight on the model probability; (1 - weight) goes to the rules subscore.",
    )
    band_thresholds: dict[RiskBand, float] = Field(
        default_factory=lambda: dict(_DEFAULT_BAND_THRESHOLDS),
        description="Per-band cumulative lower bounds; the highest band whose bound <= score wins.",
    )
    alert_threshold: float = Field(
        default=_DEFAULT_ALERT_THRESHOLD,
        ge=0.0,
        le=1.0,
        description="Combined-score threshold at/above which the run raises an alert.",
    )

    def blend(self, *, fraud_probability: float, rules_subscore: Decimal | float) -> float:
        """Return the convex blend of the model probability + rules subscore (clamped to [0,1])."""
        blended = self.model_weight * fraud_probability + (1.0 - self.model_weight) * float(
            rules_subscore
        )
        return min(_MAX_SCORE, max(_MIN_SCORE, blended))

    def band_for(self, combined_score: float) -> RiskBand:
        """Return the highest band whose lower bound is <= the score (LOW on empty/bad config)."""
        eligible = [band for band, lower in self.band_thresholds.items() if combined_score >= lower]
        if not eligible:
            return RiskBand.LOW
        return max(eligible, key=lambda band: self.band_thresholds[band])

    def assess(
        self, *, fraud_probability: float, rules_subscore: Decimal | float
    ) -> RiskAssessment:
        """Blend the two subscores, band the result, and decide whether to raise an alert."""
        combined = self.blend(fraud_probability=fraud_probability, rules_subscore=rules_subscore)
        return RiskAssessment(
            combined_score=combined,
            risk_band=self.band_for(combined),
            alert=combined >= self.alert_threshold,
        )

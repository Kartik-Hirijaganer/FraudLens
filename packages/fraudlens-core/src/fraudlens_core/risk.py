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
- ModelRiskThresholds: a model version's calibrated-probability operating points (med/high/crit).
- RiskPolicy: the blend weight, cumulative band thresholds, and alert threshold (tunable).
- RiskAssessment: the blended `combined_score`, the resolved `RiskBand`, and the alert flag.

Key functions:
- (none)

Notes:
- The blend is a convex combination `w*model + (1-w)*rules` then clamped to [0, 1].
  This deterministic policy score is used for banding; it is not a calibrated probability.
- Banding picks the HIGHEST band whose lower bound is <= the score; an empty/garbled threshold
  map falls back to `RiskBand.LOW`, so banding never raises on bad config (graceful degradation).
- `RiskPolicy` defaults are the canonical source the seed mirrors; changing them here changes
  the documented defaults (no duplicated magic thresholds across the codebase, rule 5).
- `ModelRiskThresholds` maps rare-event probabilities into policy-score space using persisted
  operating points. Normalization anchors target `band_bound / model_weight`, clamped to [0, 1]
  and made nondecreasing. Changing the weight also changes normalization and saturation. The
  model contribution cannot exceed its weight; at the default 0.7, CRITICAL at 0.85 requires
  rule contribution. `None` thresholds keep the identity mapping for older artifacts/fixtures.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fraudlens_core.types import RiskBand

# Clamp bounds for the blended score (named so they are not magic literals, rule 4 / PLR2004).
_MIN_SCORE = 0.0
_MAX_SCORE = 1.0

# Documented default blend/banding policy — mirrored by the seeded global `system_config`
# (`riskBandThresholds` / `alertThreshold` / `riskBlendModelWeight`). These stay the documented
# in-process fallbacks a DB outage degrades to, so a single source of truth lives here
# (plan §10.1 / §9.1).
_DEFAULT_MODEL_WEIGHT = 0.7
_DEFAULT_BAND_THRESHOLDS: dict[RiskBand, float] = {
    RiskBand.LOW: 0.0,
    RiskBand.MEDIUM: 0.3,
    RiskBand.HIGH: 0.6,
    RiskBand.CRITICAL: 0.85,
}
_DEFAULT_ALERT_THRESHOLD = 0.6


class ModelRiskThresholds(BaseModel):
    """A model version's calibrated-probability operating points with recorded provenance.

    New temporal bundles derive these from calibration; older served bundles may record
    `threshold_source=holdout`. Read the artifact metadata rather than assuming a clean test.

    `medium`/`high`/`critical` are the calibrated probabilities at/above which THIS model's
    signal alone warrants that band (top-K/percentile operating points chosen at training time,
    e.g. the alert-budget and top-slice quantiles). They must be strictly increasing inside
    (0, 1) — a degenerate score distribution must omit thresholds rather than persist junk.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    medium: float = Field(
        ..., gt=0.0, lt=1.0, description="Probability at which the model signal warrants MEDIUM."
    )
    high: float = Field(
        ..., gt=0.0, lt=1.0, description="Probability at which the model signal warrants HIGH."
    )
    critical: float = Field(
        ...,
        gt=0.0,
        lt=1.0,
        description="Probability at which the model signal warrants CRITICAL.",
    )

    @model_validator(mode="after")
    def _strictly_increasing(self) -> ModelRiskThresholds:
        """Require medium < high < critical so the normalization map stays monotone."""
        if not (self.medium < self.high < self.critical):
            raise ValueError("model risk thresholds must satisfy medium < high < critical")
        return self


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

    def _band_bound(self, band: RiskBand) -> float:
        """Return a band's configured lower bound, falling back to the documented default."""
        return float(self.band_thresholds.get(band, _DEFAULT_BAND_THRESHOLDS[band]))

    def model_risk(self, fraud_probability: float, thresholds: ModelRiskThresholds | None) -> float:
        """Normalize a calibrated probability into model-risk space via the operating points.

        Identity (clamped) when no thresholds exist or the model carries no blend weight — the
        legacy behavior every pre-v2 artifact keeps. With thresholds, a monotone piecewise-linear
        map targets `bound / model_weight` at each operating point, capped at 1.0 and made
        nondecreasing. Without clipping, zero-rule model contribution reaches the band's lower
        bound. A bound above the model weight requires additional rule contribution.
        """
        probability = min(_MAX_SCORE, max(_MIN_SCORE, fraud_probability))
        if thresholds is None or self.model_weight <= 0.0:
            return probability
        xs = (0.0, thresholds.medium, thresholds.high, thresholds.critical, 1.0)
        targets = (
            0.0,
            self._band_bound(RiskBand.MEDIUM) / self.model_weight,
            self._band_bound(RiskBand.HIGH) / self.model_weight,
            self._band_bound(RiskBand.CRITICAL) / self.model_weight,
            1.0,
        )
        ys: list[float] = []
        for target in targets:  # clamp to [0,1] and force non-decreasing (bad config safe)
            level = min(_MAX_SCORE, max(_MIN_SCORE, target))
            ys.append(max(level, ys[-1]) if ys else level)
        for index in range(1, len(xs)):  # xs strictly increase (validator + 0/1 sentinels)
            if probability <= xs[index]:
                span = xs[index] - xs[index - 1]
                fraction = (probability - xs[index - 1]) / span
                return ys[index - 1] + fraction * (ys[index] - ys[index - 1])
        return _MAX_SCORE

    def band_for(self, combined_score: float) -> RiskBand:
        """Return the highest band whose lower bound is <= the score (LOW on empty/bad config)."""
        eligible = [band for band, lower in self.band_thresholds.items() if combined_score >= lower]
        if not eligible:
            return RiskBand.LOW
        return max(eligible, key=lambda band: self.band_thresholds[band])

    def assess(
        self,
        *,
        fraud_probability: float,
        rules_subscore: Decimal | float,
        model_thresholds: ModelRiskThresholds | None = None,
    ) -> RiskAssessment:
        """Blend the two subscores, band the result, and decide whether to raise an alert.

        `model_thresholds` (the scoring model's persisted operating points) normalizes the raw
        calibrated probability into model-risk space first; omitted, the blend consumes the raw
        probability exactly as before.
        """
        combined = self.blend(
            fraud_probability=self.model_risk(fraud_probability, model_thresholds),
            rules_subscore=rules_subscore,
        )
        return RiskAssessment(
            combined_score=combined,
            risk_band=self.band_for(combined),
            alert=combined >= self.alert_threshold,
        )

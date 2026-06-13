"""Summary: Deterministic, PHI-free feature extraction for the fraud scorer (plan §16
Phase 5). It turns the SAME PHI-free analytical view the rules engine already uses —
`fraudlens_core.RuleContext` (the transaction under review + pre-grouped same-account
history) — into the fixed-order numeric feature vector the XGBoost model and SHAP explainer
consume. Reusing `RuleContext` means the scorer never sees account identifiers (layering +
PHI hygiene by construction, plan §8.4); living in `fraudlens-ml` keeps the heavy-ML feature
logic out of `core`/`backend`. `FEATURE_NAMES` is the single ordered source of truth for the
feature space; the persisted `FeatureSpec` (model_versions.feature_spec / the artifact) is
built from it, so a model and the code that scores it can never disagree on column order.

Key classes:
- FeatureSpec: the versioned, ordered list of feature names a model was trained on.

Key functions:
- current_feature_spec: the FeatureSpec for the current FEATURE_NAMES (model-registry input).
- country_risk: the graded PHI-free risk weight for a country code (default if unseen).
- channel_risk: the graded PHI-free risk weight for a channel (default if unseen).
- extract_features: map a RuleContext to an ordered, PHI-free name->value feature mapping.
- feature_vector: order a feature mapping into the model-ready 1xN float row.
- extract_feature_vector: map a RuleContext to the model-ready 1xN float ndarray.

Notes:
- Country/channel risk are graded reference lookups (not PHI); an unknown code falls back to
a documented default so scoring never raises on an unseen value.
- Velocity/amount/country aggregates count ONLY same-account history within a fixed 24h
window before the transaction, so the features are deterministic given a context.
- All thresholds/weights are module constants (no magic values, plan §12.1 / ruff PLR2004).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timedelta
from decimal import Decimal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from fraudlens_core import RuleContext, TransactionDirection
from fraudlens_core.rules.base import RuleTransaction

FEATURE_SPEC_VERSION = 1

# The ordered feature space — the single source of truth for column order. The model
# artifact persists this so training and scoring can never disagree (plan §16 Phase 5).
FEATURE_NAMES: tuple[str, ...] = (
    "amount_log",
    "hour_of_day",
    "day_of_week",
    "is_round_amount",
    "country_risk",
    "channel_risk",
    "velocity_24h",
    "amount_24h_sum_log",
    "distinct_countries_24h",
    "is_outbound",
)

_WINDOW_24H = timedelta(hours=24)
_ROUND_AMOUNT_MODULUS = Decimal("100")

# Graded, PHI-free reference risk for known high-risk jurisdictions / channels; an unseen
# code falls back to the documented default so an unfamiliar value never breaks scoring.
_DEFAULT_COUNTRY_RISK = 0.15
_COUNTRY_RISK: dict[str, float] = {
    "US": 0.05,
    "GB": 0.10,
    "CA": 0.10,
    "DE": 0.10,
    "FR": 0.10,
    "AU": 0.10,
    "MX": 0.45,
    "BR": 0.45,
    "NG": 0.85,
    "RU": 0.85,
    "IR": 0.95,
    "KP": 0.95,
    "PA": 0.70,
    "KY": 0.70,
    "CY": 0.65,
}
_DEFAULT_CHANNEL_RISK = 0.30
_CHANNEL_RISK: dict[str, float] = {
    "card": 0.20,
    "ach": 0.30,
    "wire": 0.60,
    "swift": 0.65,
    "crypto": 0.90,
    "cash": 0.75,
}


class FeatureSpec(BaseModel):
    """The versioned, ordered list of feature names a model was trained on/scored with."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(..., ge=1, description="Feature-spec schema version (bumped on change).")
    features: list[str] = Field(..., description="Ordered feature names defining column order.")


def current_feature_spec() -> FeatureSpec:
    """Return the FeatureSpec for the current FEATURE_NAMES (persisted in the registry)."""
    return FeatureSpec(version=FEATURE_SPEC_VERSION, features=list(FEATURE_NAMES))


def country_risk(country: str) -> float:
    """Return the graded PHI-free risk weight for a country code (default if unseen)."""
    return _COUNTRY_RISK.get(country.upper(), _DEFAULT_COUNTRY_RISK)


def channel_risk(channel: str) -> float:
    """Return the graded PHI-free risk weight for a channel (default if unseen)."""
    return _CHANNEL_RISK.get(channel.lower(), _DEFAULT_CHANNEL_RISK)


def _is_round_amount(amount: Decimal) -> float:
    """Return 1.0 when the amount is a whole multiple of 100 (a round-amount signal)."""
    return 1.0 if amount % _ROUND_AMOUNT_MODULUS == 0 else 0.0


def _recent_history(context: RuleContext) -> tuple[RuleTransaction, ...]:
    """Return same-account history strictly within the 24h window before the transaction."""
    cutoff = context.transaction.occurred_at - _WINDOW_24H
    return tuple(
        prior
        for prior in context.history
        if cutoff <= prior.occurred_at < context.transaction.occurred_at
    )


def extract_features(context: RuleContext) -> dict[str, float]:
    """Map a RuleContext to an ordered, deterministic, PHI-free feature mapping."""
    txn = context.transaction
    recent = _recent_history(context)
    amount = float(txn.amount)
    window_sum = amount + sum(float(prior.amount) for prior in recent)
    countries = {txn.country, *(prior.country for prior in recent)}
    values: dict[str, float] = {
        "amount_log": float(np.log1p(amount)),
        "hour_of_day": float(txn.occurred_at.hour),
        "day_of_week": float(txn.occurred_at.weekday()),
        "is_round_amount": _is_round_amount(txn.amount),
        "country_risk": country_risk(txn.country),
        "channel_risk": channel_risk(txn.channel),
        "velocity_24h": float(len(recent)),
        "amount_24h_sum_log": float(np.log1p(window_sum)),
        "distinct_countries_24h": float(len(countries)),
        "is_outbound": 1.0 if txn.direction == TransactionDirection.OUTBOUND else 0.0,
    }
    return {name: values[name] for name in FEATURE_NAMES}


def feature_vector(
    features: Mapping[str, float], names: Sequence[str] = FEATURE_NAMES
) -> np.ndarray:
    """Order a feature mapping into a model-ready 1xN float row in the given column order.

    `names` defaults to the current spec but is passed the LOADED model's own feature order
    when scoring, so a model is always fed the exact columns it was trained on. A name the
    extractor cannot supply raises a descriptive error rather than scoring on bad data.
    """
    missing = [name for name in names if name not in features]
    if missing:
        raise ValueError(f"feature mapping is missing required features: {missing}")
    return np.array([[float(features[name]) for name in names]], dtype=np.float64)


def extract_feature_vector(context: RuleContext) -> np.ndarray:
    """Map a RuleContext directly to the model-ready 1xN float feature row."""
    return feature_vector(extract_features(context))

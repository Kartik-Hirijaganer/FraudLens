"""Summary: The deterministic synthetic IEEE-CIS-shaped training dataset shared by
`scripts/train_model.py` and `scripts/train_baseline.py`. It remains the system of record for
`make train-model`, CI, the committed fixture, and retraining; opt-in public-dataset training
lives separately in `lib.aml_fraud`. This generator samples transaction attributes (amount,
hour, country/
channel risk, velocity, …) and concentrates fraud in nonlinear AND-interactions of those
features — large cross-border amounts, rapid movement on risky channels, round-amount
structuring — so a tree model (XGBoost) can separate it AND beat a linear logistic baseline,
at a low (~3.5%) base rate. Columns are emitted in `fraudlens_ml` `FEATURE_NAMES` order, so the
trained model's columns line up exactly with what the scorer extracts from a real transaction.
The `DataSplit` container + the seeded random `split_dataset` now live in `lib.dataset` (shared
with the real AML loader); this module keeps only the synthetic generator.

Key classes:
- (none)

Key functions:
- generate_dataset: sample (X, y) with fraud concentrated in nonlinear feature interactions.

Notes:
- Everything is seeded (numpy default_rng): same (n, seed) -> identical X and y, so training
  and the gate tests are reproducible across runs and machines.
- The intercept is a tuned constant chosen for a ~3.5% base rate; the exact rate drifts a
  little with n but stays low, which is all the gates require.
- The data is synthetic and PHI-free by construction (no identifiers, no real accounts), so a
  derived dataset manifest carries only feature names (plan §9.4 / ADR-015).
"""

from __future__ import annotations

import numpy as np

from fraudlens_ml.scoring.features import FEATURE_NAMES

# Tuned for a ~3.5% base rate at the signal scale below (see plan §10.5.1 base-rate note).
_SIGNAL_INTERCEPT = -5.9068
_SIGNAL_SCALE = 2.2

# Comparison thresholds defining the high-risk feature regions (named; ruff PLR2004).
_HI_AMOUNT_LOG = 5.2
_HI_COUNTRY_RISK = 0.45
_HI_CHANNEL_RISK = 0.40
_HI_VELOCITY = 3
_ODD_HOUR_START = 22
_ODD_HOUR_END = 6
_ROUND_RATE = 0.15
_OUTBOUND_RATE = 0.70

# Feature-spec v2 sampling knobs (named; ruff PLR2004). Values mirror the live extractor's
# semantics: the burstiness sentinel is the 24h window itself; fan-in spikes with the
# rapid-movement interaction so the counterparty signal co-moves with fraud.
_INBOUND_SHARE = 0.45
_PREV_TXN_SCALE_SECONDS = 14_400.0
_NO_PRIOR_SENTINEL_SECONDS = 86_400.0
_FAN_IN_BOOST = 2.5


def generate_dataset(n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Sample (X, y) with fraud concentrated in nonlinear feature interactions (deterministic)."""
    rng = np.random.default_rng(seed)
    amount = rng.lognormal(3.8, 1.1, n)
    hour = rng.integers(0, 24, n)
    day_of_week = rng.integers(0, 7, n)
    is_round = (rng.random(n) < _ROUND_RATE).astype(float)
    country_risk = np.clip(rng.beta(1.4, 6.0, n), 0.0, 1.0)
    channel_risk = np.clip(rng.beta(1.6, 5.0, n), 0.0, 1.0)
    velocity = rng.poisson(1.2, n).astype(float)
    amount_24h_sum = amount * (velocity + rng.random(n))
    distinct_countries = np.minimum(velocity, rng.poisson(0.5, n)).astype(float)
    is_outbound = (rng.random(n) < _OUTBOUND_RATE).astype(float)
    amount_log = np.log1p(amount)
    odd_hour = ((hour < _ODD_HOUR_END) | (hour >= _ODD_HOUR_START)).astype(float)

    hi_amount = (amount_log > _HI_AMOUNT_LOG).astype(float)
    hi_country = (country_risk > _HI_COUNTRY_RISK).astype(float)
    hi_channel = (channel_risk > _HI_CHANNEL_RISK).astype(float)
    hi_velocity = (velocity > _HI_VELOCITY).astype(float)
    score = (
        3.2 * hi_amount * hi_country
        + 2.8 * hi_velocity * hi_channel
        + 2.2 * hi_amount * odd_hour
        + 1.8 * is_round * hi_amount
        + 2.0 * hi_country * is_outbound
        + 1.5 * hi_channel * hi_country
    )
    logit = _SIGNAL_INTERCEPT + _SIGNAL_SCALE * score + 0.15 * (amount_log - 3.8)
    probability = 1.0 / (1.0 + np.exp(-logit))
    labels = (rng.random(n) < probability).astype(int)

    # Feature-spec v2 behavioral/counterparty columns. They are sampled AFTER the label draw so
    # the label mechanism above is untouched; their fraud correlation flows through the existing
    # velocity/channel drivers (dest fan-in spikes on the rapid-movement interaction), mirroring
    # how the real extractor's window features co-move.
    inbound_velocity = rng.binomial(velocity.astype(int), _INBOUND_SHARE).astype(float)
    inbound_amount_sum = amount * inbound_velocity * rng.random(n)
    seconds_since_prev = np.where(
        velocity > 0,
        np.minimum(
            rng.exponential(_PREV_TXN_SCALE_SECONDS, n) / (1.0 + velocity),
            _NO_PRIOR_SENTINEL_SECONDS,
        ),
        _NO_PRIOR_SENTINEL_SECONDS,
    )
    distinct_channels = 1.0 + np.minimum(velocity, rng.poisson(0.4, n)).astype(float)
    round_share = (is_round + rng.binomial(velocity.astype(int), _ROUND_RATE)) / (velocity + 1.0)
    dest_fan_in = rng.poisson(0.5 + _FAN_IN_BOOST * hi_velocity * hi_channel, n).astype(float)
    dest_inbound_sum = amount * (1.0 + dest_fan_in * rng.random(n))
    # A layering hop forwards what it gathers: outbound activity scales with fan-in.
    dest_outbound_velocity = rng.binomial(dest_fan_in.astype(int), _INBOUND_SHARE).astype(float)
    dest_outbound_sum = dest_inbound_sum * rng.random(n) * (dest_outbound_velocity > 0)

    columns = {
        "amount_log": amount_log,
        "hour_of_day": hour.astype(float),
        "day_of_week": day_of_week.astype(float),
        "is_round_amount": is_round,
        "country_risk": country_risk,
        "channel_risk": channel_risk,
        "velocity_24h": velocity,
        "amount_24h_sum_log": np.log1p(amount_24h_sum),
        "distinct_countries_24h": distinct_countries,
        "is_outbound": is_outbound,
        "inbound_velocity_24h": inbound_velocity,
        "inbound_amount_24h_log": np.log1p(inbound_amount_sum),
        "seconds_since_prev_txn_log": np.log1p(seconds_since_prev),
        "distinct_channels_24h": distinct_channels,
        "round_amount_share_24h": round_share,
        "dest_fan_in_24h": dest_fan_in,
        "dest_inbound_amount_24h_log": np.log1p(dest_inbound_sum),
        "dest_outbound_velocity_24h": dest_outbound_velocity,
        "dest_outbound_amount_24h_log": np.log1p(dest_outbound_sum),
    }
    features = np.column_stack([columns[name] for name in FEATURE_NAMES])
    return features, labels

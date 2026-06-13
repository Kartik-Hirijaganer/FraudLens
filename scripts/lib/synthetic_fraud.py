"""Summary: The deterministic synthetic IEEE-CIS-shaped training dataset shared by the two
Phase 5 training scripts (`scripts/train_model.py`, `scripts/train_baseline.py`). FraudLens
ships no real IEEE-CIS download and never stores real PHI (governance), so `make train-model`
must produce a model from data that is fully synthetic, reproducible, and learnable enough to
clear the §10.5.1 gates. This generator samples transaction attributes (amount, hour, country/
channel risk, velocity, …) and concentrates fraud in nonlinear AND-interactions of those
features — large cross-border amounts, rapid movement on risky channels, round-amount
structuring — so a tree model (XGBoost) can separate it AND beat a linear logistic baseline,
at a low (~3.5%) base rate. Columns are emitted in `fraudlens_ml` `FEATURE_NAMES` order, so the
trained model's columns line up exactly with what the scorer extracts from a real transaction.

Key classes:
- DataSplit: the deterministic train / calibration / holdout arrays for one dataset.

Key functions:
- generate_dataset: sample (X, y) with fraud concentrated in nonlinear feature interactions.
- split_dataset: deterministically split (X, y) into train / calibration / holdout folds.

Notes:
- Everything is seeded (numpy default_rng): same (n, seed) -> identical X, y, and folds, so
  training and the gate tests are reproducible across runs and machines.
- The intercept is a tuned constant chosen for a ~3.5% base rate; the exact rate drifts a
  little with n but stays low, which is all the gates require.
- The data is synthetic and PHI-free by construction (no identifiers, no real accounts), so a
  derived dataset manifest carries only feature names (plan §9.4 / ADR-015).
"""

from __future__ import annotations

from dataclasses import dataclass

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

# Calibration + holdout folds are each this fraction of the dataset; the rest trains.
_HOLDOUT_FRACTION = 0.2
_CALIBRATION_FRACTION = 0.2


@dataclass(frozen=True)
class DataSplit:
    """The deterministic train / calibration / holdout folds for one synthetic dataset."""

    x_train: np.ndarray
    y_train: np.ndarray
    x_calibration: np.ndarray
    y_calibration: np.ndarray
    x_holdout: np.ndarray
    y_holdout: np.ndarray


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
    }
    features = np.column_stack([columns[name] for name in FEATURE_NAMES])
    return features, labels


def split_dataset(features: np.ndarray, labels: np.ndarray, seed: int) -> DataSplit:
    """Deterministically split (X, y) into train / calibration / holdout folds."""
    n = features.shape[0]
    order = np.random.default_rng(seed).permutation(n)
    n_holdout = int(n * _HOLDOUT_FRACTION)
    n_calibration = int(n * _CALIBRATION_FRACTION)
    holdout = order[:n_holdout]
    calibration = order[n_holdout : n_holdout + n_calibration]
    train = order[n_holdout + n_calibration :]
    return DataSplit(
        x_train=features[train],
        y_train=labels[train],
        x_calibration=features[calibration],
        y_calibration=labels[calibration],
        x_holdout=features[holdout],
        y_holdout=labels[holdout],
    )

"""Summary: The neutral home for the `DataSplit` container and the seeded RANDOM split helper,
lifted out of `synthetic_fraud.py` (real-AML training plan Phase 3). Keeping them here lets the
real AML loader (`scripts/lib/aml_fraud.py`) build the SAME `DataSplit` shape via a
chronological split without importing the synthetic generator — the two dataset paths share one
split container but choose their split strategy independently. `train_candidate(split, gates,
seed=...)` consumes `DataSplit` unchanged, so neither trainer nor the fixture is affected.

Key classes:
- DataSplit: the deterministic train / calibration / holdout arrays for one dataset.

Key functions:
- split_dataset: deterministically split (X, y) into train / calibration / holdout folds (seeded).

Notes:
- The synthetic path uses this seeded RANDOM permutation split (unchanged, for CI determinism);
  the real AML path uses `aml_fraud.split_chronological` instead, which never random-samples
  rare AML patterns and keeps an account's transactions inside one fold.
- Calibration + holdout folds are each `_*_FRACTION` of the dataset; the remainder trains.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Calibration + holdout folds are each this fraction of the dataset; the rest trains. Shared by
# the synthetic (random) and real (chronological) split strategies so both fold the same way.
_HOLDOUT_FRACTION = 0.2
_CALIBRATION_FRACTION = 0.2


@dataclass(frozen=True)
class DataSplit:
    """The deterministic train / calibration / holdout folds for one dataset."""

    x_train: np.ndarray
    y_train: np.ndarray
    x_calibration: np.ndarray
    y_calibration: np.ndarray
    x_holdout: np.ndarray
    y_holdout: np.ndarray


def split_dataset(features: np.ndarray, labels: np.ndarray, seed: int) -> DataSplit:
    """Deterministically split (X, y) into train / calibration / holdout folds (seeded random)."""
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

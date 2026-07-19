"""Summary: Strict chronological timestamp-cohort folds for the offline GFP benchmark
(GFP plan Phase 3). Rows sorted by (timestamp, originalRowId) are split 60/20/20 by the
configured EXACT rational fractions, with each nominal boundary advanced FORWARD through
any equal-timestamp cohort so no timestamp ever spans two folds. This is deliberately a
separate implementation from production's account-grouped `split_chronological`
(scripts/lib/aml_fraud.py), which the plan forbids touching: GFP folds are frozen BEFORE
any graph call and graph state flows train -> calibration -> holdout, never backward.

Key classes:
- (none)

Key functions:
- assign_timestamp_cohort_folds: sorted epoch seconds -> per-row fold ids (0/1/2).
- fold_assignment_record: build the frozen FoldAssignment provenance record.

Notes:
- Fold ids are FOLD_TRAIN=0, FOLD_CALIBRATION=1, FOLD_HOLDOUT=2 across the package.
- Boundary advance direction is deterministic: a cohort straddling a nominal boundary
  lands WHOLLY in the earlier fold, which can leave a later fold empty on degenerate
  inputs — samplers fail loudly on missing classes rather than resizing (the contract).
- Inputs must already be sorted; the function rejects unsorted timestamps instead of
  silently reordering (ordering is owned by the edge builder).
"""

from __future__ import annotations

from fractions import Fraction

import numpy as np

from lib.gfp.boundaries import FoldAssignment

FOLD_TRAIN = 0
FOLD_CALIBRATION = 1
FOLD_HOLDOUT = 2
_FOLD_COUNT = 3


def _advance_through_cohort(times: np.ndarray, nominal: int) -> int:
    """Advance a nominal boundary index forward past any equal-timestamp cohort."""
    n = times.shape[0]
    boundary = nominal
    while 0 < boundary < n and times[boundary] == times[boundary - 1]:
        boundary += 1
    return boundary


def assign_timestamp_cohort_folds(
    utc_epoch_s: np.ndarray, fractions: tuple[Fraction, Fraction, Fraction]
) -> np.ndarray:
    """Assign cohort-safe chronological folds over (timestamp, originalRowId)-sorted rows.

    `fractions` are the exact train/calibration/holdout rationals (they must sum to 1 —
    the config layer already guarantees it). Returns an int8 array of fold ids.
    """
    times = np.asarray(utc_epoch_s)
    n = int(times.shape[0])
    if n == 0:
        raise ValueError("cannot assign folds over zero rows")
    if np.any(np.diff(times) < 0):
        raise ValueError("fold assignment requires (timestamp, originalRowId)-sorted rows")
    train_fraction, calibration_fraction, holdout_fraction = fractions
    if train_fraction + calibration_fraction + holdout_fraction != 1:
        raise ValueError("fold fractions must sum to exactly 1")
    nominal_train_end = int(n * train_fraction)
    nominal_calibration_end = int(n * (train_fraction + calibration_fraction))
    train_end = _advance_through_cohort(times, nominal_train_end)
    calibration_end = _advance_through_cohort(times, max(nominal_calibration_end, train_end))
    folds = np.full(n, FOLD_HOLDOUT, dtype=np.int8)
    folds[:train_end] = FOLD_TRAIN
    folds[train_end:calibration_end] = FOLD_CALIBRATION
    return folds


def fold_assignment_record(
    utc_epoch_s: np.ndarray,
    labels: np.ndarray,
    folds: np.ndarray,
    fractions: tuple[str, str, str],
) -> FoldAssignment:
    """Build the frozen FoldAssignment provenance record for one dataset's fold split."""
    times = np.asarray(utc_epoch_s)
    sizes: list[int] = []
    positives: list[int] = []
    boundaries: list[int] = []
    for fold_id in (FOLD_TRAIN, FOLD_CALIBRATION, FOLD_HOLDOUT):
        mask = folds == fold_id
        sizes.append(int(mask.sum()))
        positives.append(int(np.asarray(labels)[mask].sum()))
        if fold_id != FOLD_HOLDOUT:
            boundaries.append(int(times[mask][-1]) if mask.any() else 0)
    return FoldAssignment(
        fractions=fractions,
        boundary_epochs_s=(boundaries[0], boundaries[1]),
        fold_sizes=(sizes[0], sizes[1], sizes[2]),
        fold_positive_counts=(positives[0], positives[1], positives[2]),
    )

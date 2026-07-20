"""Summary: Label-blind node-induced context selection + post-feature target
stratification for the offline GFP benchmark (GFP plan Phase 3). A node is kept when the
SHA-256 of its normalized `(bank, account)` key falls inside the configured EXACT hash
fraction; an edge survives only when BOTH endpoints are kept — a topology-preserving
subgraph, never random row sampling. Medium CSVs are streamed in chunks (no full frame,
no mmap, no resumable cache). If any chronological fold lacks a label class, selection
escalates one ladder step (1/4 -> 1/3 -> 1/2) and finally FAILS — never a silent resize.
Targets are then stratified per fold with `sample_frame` semantics (per-class rounded
quotas, seeded generator), matching each source fold's illicit ratio.

Key classes:
- InsufficientClassError: raised when the full ladder still leaves a fold single-class.
- ContextSelection: one accepted selection (frame + fraction + per-fold class counts).

Key functions:
- node_keep_mask: exact-rational SHA-256 keep decisions for account keys (label-blind).
- select_context_frame: stream one CSV in chunks, keeping both-endpoint-kept edges.
- fold_class_counts: per-fold (positive counts, sizes) in train/calibration/holdout order.
- select_context_with_escalation: walk the ladder until folds carry both classes.
- stratify_targets: per-fold, per-class seeded target quotas over the retained context.

Notes:
- Selection reads ONLY bank/account columns for the keep decision — labels play no part
(label-blind by construction; the class check afterwards only validates fold makeup).
- Fold class checks and target stratification run on the SERVABLE, sorted context via
`lib.gfp.edges` ordering (timestamp, originalRowId), the same order GFP consumes.
- `stratify_targets` mirrors `lib.aml_fraud.sample_frame` semantics: per-class
`min(len, max(1, round(len * fraction)))` with one seeded Generator, deterministic
iteration order, and every row kept when the quota covers the fold.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd

from lib.aml_fraud import IBM_AML, source_columns
from lib.aml_mapping import ibm_account_key
from lib.gfp.folds import FOLD_CALIBRATION, FOLD_HOLDOUT, FOLD_TRAIN

_HASH_BITS = 256
_HASH_SPAN = 1 << _HASH_BITS
_CHUNK_ROWS_DEFAULT = 1_000_000  # streaming CSV chunk bound (medium files never fit RAM)

# IBM column headers the keep decision reads (bank/account only — label-blind).
_COL_FROM_BANK = "From Bank"
_COL_FROM_ACCOUNT = "Account"
_COL_TO_BANK = "To Bank"
_COL_TO_ACCOUNT = "Account.1"

_FOLD_IDS: tuple[int, ...] = (FOLD_TRAIN, FOLD_CALIBRATION, FOLD_HOLDOUT)


class InsufficientClassError(RuntimeError):
    """The whole escalation ladder still left a fold without both label classes."""


# Maps a retained raw frame to its (labels, folds) in GFP edge order (injected so this
# module never depends on the edge builder).
FoldLabelBuilder = Callable[[pd.DataFrame], tuple[np.ndarray, np.ndarray]]


@dataclass(frozen=True)
class ContextSelection:
    """One accepted node-induced selection: the frame, the fraction, fold class counts."""

    frame: pd.DataFrame
    fraction: Fraction
    fold_positive_counts: tuple[int, int, int]
    fold_sizes: tuple[int, int, int]


def _keep_key(key: str, fraction: Fraction) -> bool:
    """Exact-rational keep decision: SHA-256(key) / 2^256 < fraction (label-blind)."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    value = int.from_bytes(digest, "big")
    return value * fraction.denominator < fraction.numerator * _HASH_SPAN


def node_keep_mask(keys: list[str], fraction: Fraction) -> np.ndarray:
    """Vectorize `_keep_key` over normalized account keys."""
    return np.array([_keep_key(key, fraction) for key in keys], dtype=bool)


def _chunk_keep_mask(chunk: pd.DataFrame, fraction: Fraction) -> np.ndarray:
    """Keep a chunk row only when BOTH endpoint nodes fall inside the hash fraction."""
    source_keys = [
        ibm_account_key(str(bank), str(account))
        for bank, account in zip(chunk[_COL_FROM_BANK], chunk[_COL_FROM_ACCOUNT], strict=True)
    ]
    dest_keys = [
        ibm_account_key(str(bank), str(account))
        for bank, account in zip(chunk[_COL_TO_BANK], chunk[_COL_TO_ACCOUNT], strict=True)
    ]
    both: np.ndarray = node_keep_mask(source_keys, fraction) & node_keep_mask(dest_keys, fraction)
    return both


def select_context_frame(
    csv_path: Path, fraction: Fraction, *, chunk_rows: int = _CHUNK_ROWS_DEFAULT
) -> pd.DataFrame:
    """Stream the CSV in chunks, retaining only both-endpoint-kept edges (label-blind).

    Reads the same column set the production loader consumes (`source_columns`), string
    typed, so the retained frame feeds both the GFP edge builder and the Arm-A features.
    """
    kept: list[pd.DataFrame] = []
    reader = pd.read_csv(
        csv_path, usecols=list(source_columns(IBM_AML)), dtype=str, chunksize=chunk_rows
    )
    for chunk in reader:
        mask = _chunk_keep_mask(chunk, fraction)
        if mask.any():
            kept.append(chunk.iloc[np.flatnonzero(mask)])
    if not kept:
        return pd.DataFrame(columns=list(source_columns(IBM_AML)))
    return pd.concat(kept, ignore_index=True)


def fold_class_counts(
    labels: np.ndarray, folds: np.ndarray
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Return (per-fold positive counts, per-fold sizes) in train/cal/holdout order."""
    labels_arr = np.asarray(labels)
    folds_arr = np.asarray(folds)
    positives: list[int] = []
    sizes: list[int] = []
    for fold_id in _FOLD_IDS:
        mask = folds_arr == fold_id
        sizes.append(int(mask.sum()))
        positives.append(int(labels_arr[mask].sum()))
    return (positives[0], positives[1], positives[2]), (sizes[0], sizes[1], sizes[2])


def _every_fold_has_both_classes(
    positives: tuple[int, int, int], sizes: tuple[int, int, int]
) -> bool:
    """True when every fold contains at least one positive AND one negative row."""
    return all(0 < pos < size for pos, size in zip(positives, sizes, strict=True))


def select_context_with_escalation(
    csv_path: Path,
    ladder: tuple[Fraction, ...],
    build_fold_labels: FoldLabelBuilder,
    *,
    chunk_rows: int = _CHUNK_ROWS_DEFAULT,
) -> ContextSelection:
    """Walk the hash-fraction ladder until every chronological fold has both classes.

    `build_fold_labels` maps a retained raw frame to its (labels, folds) in GFP edge
    order — injected so this module stays free of the edge builder's heavy lifting. After
    the last ladder step the selection FAILS (`InsufficientClassError`): the contract
    forbids silently resizing the protocol.
    """
    attempts: list[str] = []
    for fraction in ladder:
        frame = select_context_frame(csv_path, fraction, chunk_rows=chunk_rows)
        if len(frame) > 0:
            labels, folds = build_fold_labels(frame)
            positives, sizes = fold_class_counts(labels, folds)
            if _every_fold_has_both_classes(positives, sizes):
                return ContextSelection(
                    frame=frame,
                    fraction=fraction,
                    fold_positive_counts=positives,
                    fold_sizes=sizes,
                )
            attempts.append(f"{fraction}: folds pos={positives} sizes={sizes}")
        else:
            attempts.append(f"{fraction}: empty selection")
    raise InsufficientClassError(
        "node-induced selection exhausted the hash-fraction ladder without a valid fold "
        f"split — never silently resized. Attempts: {attempts}"
    )


def stratify_targets(
    labels: np.ndarray,
    folds: np.ndarray,
    fold_quotas: tuple[int, int, int],
    *,
    seed: int,
) -> np.ndarray:
    """Pick stratified targets per fold with `sample_frame` semantics; return a bool mask.

    Per fold: when the quota covers the fold, every row is a target; otherwise each label
    class contributes `min(len, max(1, round(len * quota / fold_size)))` seeded picks, so
    the target set matches the source fold's illicit ratio. One Generator drawn in a fixed
    fold-then-class order keeps the mask deterministic for a given seed.
    """
    labels_arr = np.asarray(labels)
    folds_arr = np.asarray(folds)
    mask = np.zeros(labels_arr.shape[0], dtype=bool)
    generator = np.random.default_rng(seed)
    for fold_id, quota in zip(_FOLD_IDS, fold_quotas, strict=True):
        if quota <= 0:
            raise ValueError(f"fold {fold_id} quota must be positive, got {quota}")
        fold_rows = np.flatnonzero(folds_arr == fold_id)
        if fold_rows.shape[0] == 0:
            raise ValueError(f"fold {fold_id} is empty — folds must be validated first")
        if quota >= fold_rows.shape[0]:
            mask[fold_rows] = True
            continue
        fraction = quota / fold_rows.shape[0]
        for class_value in np.unique(labels_arr[fold_rows]):
            class_rows = fold_rows[labels_arr[fold_rows] == class_value]
            take = min(class_rows.shape[0], max(1, round(class_rows.shape[0] * fraction)))
            picked = generator.choice(class_rows, size=take, replace=False)
            mask[picked] = True
    return mask

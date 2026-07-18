"""GFP sampling + fold tests (GFP plan Phase 3): label-blind exact-rational node hashing,
both-endpoint node-induced selection streamed in chunks, ladder escalation that FAILS
(never resizes) after 1/2, cohort-safe chronological folds (no timestamp spans folds),
and sample_frame-semantics target stratification (per-class rounded quotas, seeded,
deterministic, ratio-preserving)."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from lib.gfp.boundaries import FoldAssignment
from lib.gfp.folds import (
    FOLD_CALIBRATION,
    FOLD_HOLDOUT,
    FOLD_TRAIN,
    assign_timestamp_cohort_folds,
    fold_assignment_record,
)
from lib.gfp.sampling import (
    ContextSelection,
    InsufficientClassError,
    fold_class_counts,
    node_keep_mask,
    select_context_frame,
    select_context_with_escalation,
    stratify_targets,
)

_THIRDS = (Fraction(3, 5), Fraction(1, 5), Fraction(1, 5))


def _write_csv(path: Path, rows: list[tuple[str, str, str, str, str, str, str, str, str]]) -> None:
    header = (
        "Timestamp,From Bank,Account,To Bank,Account.1,Amount Paid,"
        "Payment Currency,Payment Format,Is Laundering"
    )
    lines = [header] + [",".join(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _csv_row(
    ts: str, from_bank: str, from_acc: str, to_bank: str, to_acc: str, label: str
) -> tuple[str, str, str, str, str, str, str, str, str]:
    return (ts, from_bank, from_acc, to_bank, to_acc, "100.00", "US Dollar", "Wire", label)


# ---------------------------------------------------------------------------- folds
def test_folds_split_60_20_20_and_never_split_a_timestamp_cohort() -> None:
    times = np.array([1, 1, 1, 1, 1, 1, 2, 2, 3, 3])  # cohort of six 1s straddles 60%
    folds = assign_timestamp_cohort_folds(times, _THIRDS)
    assert folds.tolist() == [0, 0, 0, 0, 0, 0, 1, 1, 2, 2]
    plain = assign_timestamp_cohort_folds(np.arange(10), _THIRDS)
    assert plain.tolist() == [0, 0, 0, 0, 0, 0, 1, 1, 2, 2]


def test_fold_boundaries_advance_forward_through_cohorts() -> None:
    # The nominal calibration boundary (index 8) falls INSIDE the trailing 9-cohort: the
    # whole cohort lands in CALIBRATION (the earlier fold), leaving holdout empty.
    times = np.array([1, 2, 3, 4, 5, 6, 7, 9, 9, 9])
    folds = assign_timestamp_cohort_folds(times, _THIRDS)
    assert folds.tolist() == [0, 0, 0, 0, 0, 0, 1, 1, 1, 1]


def test_folds_reject_unsorted_and_empty_input() -> None:
    with pytest.raises(ValueError, match="sorted"):
        assign_timestamp_cohort_folds(np.array([2, 1]), _THIRDS)
    with pytest.raises(ValueError, match="zero rows"):
        assign_timestamp_cohort_folds(np.array([], dtype=np.int64), _THIRDS)
    with pytest.raises(ValueError, match="sum to exactly 1"):
        bad = (Fraction(1, 2), Fraction(1, 4), Fraction(1, 8))
        assign_timestamp_cohort_folds(np.array([1, 2]), bad)


def test_fold_assignment_record_captures_boundaries_and_classes() -> None:
    times = np.arange(10)
    folds = assign_timestamp_cohort_folds(times, _THIRDS)
    labels = np.array([0, 1, 0, 0, 0, 0, 1, 0, 0, 1])
    record = fold_assignment_record(times, labels, folds, ("3/5", "1/5", "1/5"))
    assert isinstance(record, FoldAssignment)
    assert record.fold_sizes == (6, 2, 2)
    assert record.fold_positive_counts == (1, 1, 1)
    assert record.boundary_epochs_s == (5, 7)  # last train time, last calibration time


# ---------------------------------------------------------------------------- hashing
def test_node_keep_mask_is_deterministic_exact_and_monotone_in_the_ladder() -> None:
    keys = [f"bank\x1facct-{i}" for i in range(400)]
    quarter = node_keep_mask(keys, Fraction(1, 4))
    third = node_keep_mask(keys, Fraction(1, 3))
    half = node_keep_mask(keys, Fraction(1, 2))
    assert np.array_equal(quarter, node_keep_mask(keys, Fraction(1, 4)))  # deterministic
    assert (quarter & ~third).sum() == 0  # ladder steps only ever ADD nodes
    assert (third & ~half).sum() == 0
    assert 0 < quarter.sum() < third.sum() < half.sum() < len(keys)
    assert node_keep_mask(keys, Fraction(1, 1)).all()  # full fraction keeps everything


def test_node_keep_mask_is_label_blind() -> None:
    # The mask is a pure function of the account keys — labels never enter the decision.
    keys = ["a\x1f1", "b\x1f2", "c\x1f3"]
    half = Fraction(1, 2)
    assert np.array_equal(node_keep_mask(keys, half), node_keep_mask(list(keys), half))


# ---------------------------------------------------------------------------- selection
def test_select_context_requires_both_endpoints_and_streams_chunks(tmp_path: Path) -> None:
    rows = [
        _csv_row(
            f"2022/09/01 00:{i:02d}",
            f"B{i % 7}",
            f"S{i % 7}",
            f"B{(i + 1) % 7}",
            f"S{(i + 1) % 7}",
            "0",
        )
        for i in range(50)
    ]
    csv_path = tmp_path / "ctx.csv"
    _write_csv(csv_path, rows)
    fraction = Fraction(1, 2)
    whole = select_context_frame(csv_path, fraction, chunk_rows=1000)
    chunked = select_context_frame(csv_path, fraction, chunk_rows=7)  # forces many chunks
    pd.testing.assert_frame_equal(whole, chunked)
    # Every retained edge has BOTH endpoints kept: re-checking the mask on the result
    # keeps every row (node-induced closure).
    if len(whole) > 0:
        again = select_context_frame(csv_path, fraction, chunk_rows=3)
        assert len(again) == len(whole)


def test_select_context_returns_empty_frame_when_nothing_survives(tmp_path: Path) -> None:
    csv_path = tmp_path / "tiny.csv"
    _write_csv(csv_path, [_csv_row("2022/09/01 00:01", "B1", "S1", "B2", "S2", "0")])
    frame = select_context_frame(csv_path, Fraction(1, 10**9))
    assert len(frame) == 0


def test_escalation_walks_the_ladder_then_fails_loudly(tmp_path: Path) -> None:
    rows = [
        _csv_row(f"2022/09/01 00:{i:02d}", f"B{i}", f"S{i}", f"B{i + 1}", f"S{i + 1}", "0")
        for i in range(20)
    ]
    csv_path = tmp_path / "allneg.csv"
    _write_csv(csv_path, rows)
    seen: list[Fraction] = []

    def all_negative(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        labels = np.zeros(len(frame), dtype=np.int8)  # never a positive: every step fails
        folds = assign_timestamp_cohort_folds(np.arange(len(frame)), _THIRDS)
        return labels, folds

    ladder = (Fraction(1, 4), Fraction(1, 3), Fraction(1, 2))
    with pytest.raises(InsufficientClassError, match="never silently resized"):
        select_context_with_escalation(csv_path, ladder, all_negative)
    del seen


def test_escalation_accepts_the_first_fraction_with_both_classes(tmp_path: Path) -> None:
    rows = [
        _csv_row(
            f"2022/09/01 00:{i:02d}",
            f"B{i % 4}",
            f"S{i % 4}",
            f"B{(i + 1) % 4}",
            f"S{(i + 1) % 4}",
            "0",
        )
        for i in range(30)
    ]
    csv_path = tmp_path / "mixed.csv"
    _write_csv(csv_path, rows)
    calls: list[int] = []

    def alternating(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        calls.append(len(frame))
        labels = (np.arange(len(frame)) % 2).astype(np.int8)
        # Injected builder: a fabricated 3-way fold split keeps every fold two-class for
        # any retained size >= 6, which is what this acceptance path exercises.
        folds = (np.arange(len(frame)) % 3).astype(np.int8)
        return labels, folds

    selection = select_context_with_escalation(
        csv_path, (Fraction(1, 4), Fraction(1, 3), Fraction(1, 2)), alternating
    )
    assert isinstance(selection, ContextSelection)
    assert selection.fraction in (Fraction(1, 4), Fraction(1, 3), Fraction(1, 2))
    positives, sizes = selection.fold_positive_counts, selection.fold_sizes
    assert all(0 < p < s for p, s in zip(positives, sizes, strict=True))
    assert len(calls) >= 1


# ---------------------------------------------------------------------------- targets
def test_stratify_targets_matches_fold_ratios_with_sample_frame_semantics() -> None:
    rng = np.random.default_rng(7)
    labels = (rng.random(3000) < 0.1).astype(np.int8)
    folds = assign_timestamp_cohort_folds(np.arange(3000), _THIRDS)
    mask = stratify_targets(labels, folds, (600, 200, 200), seed=1729)
    assert np.array_equal(mask, stratify_targets(labels, folds, (600, 200, 200), seed=1729))
    for fold_id, quota in ((FOLD_TRAIN, 600), (FOLD_CALIBRATION, 200), (FOLD_HOLDOUT, 200)):
        fold_rows = folds == fold_id
        picked = mask & fold_rows
        # sample_frame semantics: per-class round() — the total lands within +-2 of quota.
        assert abs(int(picked.sum()) - quota) <= 2
        source_ratio = labels[fold_rows].mean()
        target_ratio = labels[picked].mean()
        assert target_ratio == pytest.approx(source_ratio, abs=0.02)
        assert labels[picked].sum() >= 1  # the rare class always survives (min 1)


def test_stratify_targets_keeps_everything_when_quota_covers_the_fold() -> None:
    labels = np.array([0, 1, 0, 1, 0, 0, 1, 0, 0, 0], dtype=np.int8)
    folds = assign_timestamp_cohort_folds(np.arange(10), _THIRDS)
    mask = stratify_targets(labels, folds, (600000, 200000, 200000), seed=1729)
    assert mask.all()  # quotas are caps: small folds keep every row as a target


def test_stratify_targets_rejects_empty_folds_and_bad_quotas() -> None:
    labels = np.array([0, 1, 0, 1], dtype=np.int8)
    only_train = np.zeros(4, dtype=np.int8)
    with pytest.raises(ValueError, match="empty"):
        stratify_targets(labels, only_train, (2, 1, 1), seed=1)
    folds = assign_timestamp_cohort_folds(np.arange(4), _THIRDS)
    with pytest.raises(ValueError, match="positive"):
        stratify_targets(labels, folds, (0, 1, 1), seed=1)


def test_fold_class_counts_orders_train_cal_holdout() -> None:
    labels = np.array([1, 0, 0, 1, 0, 1], dtype=np.int8)
    folds = np.array([0, 0, 1, 1, 2, 2], dtype=np.int8)
    positives, sizes = fold_class_counts(labels, folds)
    assert positives == (1, 1, 1)
    assert sizes == (2, 2, 2)

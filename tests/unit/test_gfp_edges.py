"""GFP edge-builder tests (GFP plan Phase 3): a servable IBM frame becomes a validated
GfpEdgeSet — dense first-appearance node ids, stable edge ids in (timestamp, originalRowId)
order, exact UTC seconds, USD-normalized amounts with unknown currencies REJECTED, agency
ownership via the EXISTING demo partition, cohort-safe folds, and hard invariants
(uniqueness, finiteness, <2^53, 1:1 alignment). Labels ride only in metadata — never in
the 5-column engine matrix."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from lib.aml_fraud import demo_agency_index, map_ibm_demo_row
from lib.gfp.config import load_gfp_benchmark_config
from lib.gfp.edges import GfpEdgeSet, build_gfp_edge_set, with_targets

_CONFIG = load_gfp_benchmark_config()
_AGENCIES = 3


def _frame(rows: list[dict[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, dtype=str)


def _row(  # one keyword per CSV column keeps fixtures readable
    *,
    ts: str = "2022/09/01 00:20",
    from_bank: str = "010",
    from_account: str = "A1",
    to_bank: str = "020",
    to_account: str = "B1",
    amount: str = "100.00",
    currency: str = "US Dollar",
    laundering: str = "0",
) -> dict[str, str]:
    return {
        "Timestamp": ts,
        "From Bank": from_bank,
        "Account": from_account,
        "To Bank": to_bank,
        "Account.1": to_account,
        "Amount Paid": amount,
        "Payment Currency": currency,
        "Payment Format": "Wire",
        "Is Laundering": laundering,
    }


def test_build_orders_by_timestamp_then_row_and_assigns_dense_ids() -> None:
    frame = _frame(
        [
            _row(ts="2022/09/01 00:25", from_account="A2", to_account="B2"),  # later time
            _row(ts="2022/09/01 00:20", from_account="A1", to_account="B1"),
            _row(ts="2022/09/01 00:20", from_account="A1", to_account="B2"),  # tie: row order
        ]
    )
    edges = build_gfp_edge_set(frame, _CONFIG, agency_count=_AGENCIES)
    matrix = edges.gfp_matrix
    assert matrix.shape == (3, 5)
    assert list(matrix[:, 0]) == [0.0, 1.0, 2.0]  # dense edge ids in sorted order
    assert list(edges.original_row_id) == [1, 2, 0]  # (timestamp, originalRowId) order
    assert matrix[0, 3] < matrix[2, 3]  # epoch seconds ascend
    # Dense node ids: first appearance order — A1 -> 0, B1 -> 1, B2 -> 2, A2 -> 3.
    assert list(matrix[:, 1]) == [0.0, 0.0, 3.0]
    assert list(matrix[:, 2]) == [1.0, 2.0, 2.0]
    assert edges.node_count == 4
    assert matrix.dtype == np.float64


def test_epoch_seconds_are_exact_utc() -> None:
    frame = _frame([_row(ts="2022/09/01 00:20")])
    edges = build_gfp_edge_set(frame, _CONFIG, agency_count=_AGENCIES)
    expected = int(pd.Timestamp("2022-09-01T00:20:00Z").timestamp())
    assert int(edges.gfp_matrix[0, 3]) == expected


def test_usd_normalization_uses_frozen_pins_and_rejects_unknown_currency() -> None:
    frame = _frame(
        [
            _row(amount="100.00", currency="US Dollar"),
            _row(amount="100.00", currency="Yen", from_account="A2"),
        ]
    )
    edges = build_gfp_edge_set(frame, _CONFIG, agency_count=_AGENCIES)
    assert edges.gfp_matrix[0, 4] == pytest.approx(100.0)
    assert edges.gfp_matrix[1, 4] == pytest.approx(0.75)  # 100 x pinned 0.0075
    unknown = _frame([_row(currency="Dogecoin")])
    with pytest.raises(ValueError, match="never silently treats an unknown currency"):
        build_gfp_edge_set(unknown, _CONFIG, agency_count=_AGENCIES)


def test_agency_ownership_matches_the_existing_demo_partition() -> None:
    frame = _frame(
        [
            _row(from_bank="011", to_bank="0234"),
            _row(from_bank="12", to_bank="70", ts="2022/09/01 00:21"),
        ]
    )
    edges = build_gfp_edge_set(frame, _CONFIG, agency_count=_AGENCIES)
    for position, (from_bank, to_bank) in enumerate((("011", "0234"), ("12", "70"))):
        assert edges.source_agency[position] == demo_agency_index(from_bank, _AGENCIES)
        assert edges.dest_agency[position] == demo_agency_index(to_bank, _AGENCIES)
    # And the source-agency owner equals map_ibm_demo_row's partition for the same row.
    mapped = map_ibm_demo_row(frame.iloc[0].to_dict(), 0, _AGENCIES)
    assert int(edges.source_agency[0]) == mapped.agency_index


def test_labels_stay_out_of_the_engine_matrix() -> None:
    frame = _frame([_row(laundering="1"), _row(laundering="0", ts="2022/09/01 00:21")])
    edges = build_gfp_edge_set(frame, _CONFIG, agency_count=_AGENCIES)
    assert list(edges.labels) == [1, 0]
    assert edges.gfp_matrix.shape[1] == 5  # edge_id, src, dst, time, usd — nothing else


def test_folds_are_assigned_and_recorded() -> None:
    rows = [_row(ts=f"2022/09/01 00:{20 + i:02d}", from_account=f"A{i}") for i in range(10)]
    edges = build_gfp_edge_set(_frame(rows), _CONFIG, agency_count=_AGENCIES)
    assert list(np.unique(edges.folds)) == [0, 1, 2]
    assert edges.fold_assignment.fold_sizes == (6, 2, 2)  # 3/5, 1/5, 1/5 of 10
    assert sum(edges.fold_assignment.fold_positive_counts) == int(edges.labels.sum())


def test_input_frame_is_never_mutated_and_alignment_is_one_to_one() -> None:
    frame = _frame([_row(), _row(ts="2022/09/01 00:21", from_account="A2")])
    snapshot = frame.copy(deep=True)
    edges = build_gfp_edge_set(frame, _CONFIG, agency_count=_AGENCIES)
    pd.testing.assert_frame_equal(frame, snapshot)
    assert edges.gfp_matrix.shape[0] == len(frame)
    assert sorted(edges.original_row_id.tolist()) == [0, 1]  # no loss, no duplication


def test_build_rejects_missing_columns_and_empty_frames() -> None:
    with pytest.raises(ValueError, match="missing required columns"):
        build_gfp_edge_set(pd.DataFrame({"Timestamp": ["x"]}), _CONFIG, agency_count=_AGENCIES)
    empty = _frame([_row()]).iloc[0:0]
    with pytest.raises(ValueError, match="empty frame"):
        build_gfp_edge_set(empty, _CONFIG, agency_count=_AGENCIES)


def test_matrix_invariants_reject_oversized_values() -> None:
    frame = _frame([_row(amount=str(1 << 53), currency="US Dollar")])
    with pytest.raises(ValueError, match="2\\^53"):
        build_gfp_edge_set(frame, _CONFIG, agency_count=_AGENCIES)


def test_with_targets_replaces_the_mask_and_validates_alignment() -> None:
    frame = _frame([_row(), _row(ts="2022/09/01 00:21", from_account="A2")])
    edges = build_gfp_edge_set(frame, _CONFIG, agency_count=_AGENCIES)
    assert edges.is_target.all()  # full-context default: every row is a target
    narrowed = with_targets(edges, np.array([True, False]))
    assert narrowed.is_target.tolist() == [True, False]
    assert edges.is_target.all()  # original edge set is untouched (frozen semantics)
    with pytest.raises(ValueError, match="align"):
        with_targets(edges, np.array([True]))


def test_edge_set_rejects_misaligned_metadata() -> None:
    frame = _frame([_row()])
    edges = build_gfp_edge_set(frame, _CONFIG, agency_count=_AGENCIES)
    with pytest.raises(ValueError, match="align 1:1"):
        GfpEdgeSet(
            gfp_matrix=edges.gfp_matrix,
            labels=np.array([0, 1], dtype=np.int8),  # wrong length
            source_agency=edges.source_agency,
            dest_agency=edges.dest_agency,
            folds=edges.folds,
            is_target=edges.is_target,
            original_row_id=edges.original_row_id,
            node_count=edges.node_count,
            fold_assignment=edges.fold_assignment,
        )

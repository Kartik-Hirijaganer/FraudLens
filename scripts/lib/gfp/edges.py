"""Summary: Deterministic IBM-rows -> typed-GFP-edges builder for the offline benchmark
(GFP plan Phase 3). A servable-normalized IBM frame becomes one `GfpEdgeSet`: a float64
matrix in the canonical `[edge_id, dense_src, dense_dst, utc_epoch_s, usd_amount]` wire
order plus PARALLEL metadata arrays (label, source/dest agency, fold, target flag,
originalRowId) that never enter the graph engine. Rows are ordered by
(timestamp, originalRowId); node ids are dense first-appearance ints over the SAME
`ibm_account_key` the production loader groups on; agencies reuse `demo_agency_index`
(the existing demo partition — never a second ownership model); amounts are USD-normalized
from the frozen config pins and an unknown currency FAILS the build (never assumed USD).

Key classes:
- GfpEdgeSet: the built edge matrix + parallel PHI-free metadata arrays (frozen).

Key functions:
- build_gfp_edge_set: servable IBM frame + config -> validated, fold-assigned GfpEdgeSet.
- with_targets: return a copy of an edge set with a new stratified target mask.

Notes:
- Every matrix value is asserted finite and < 2^53 (GFP consumes float64; larger ints
  would silently lose precision), edge ids are unique, node ids are dense 0..N-1, and
  the output is exactly 1:1 with the input frame (row loss/duplication is an error).
- Labels ride ONLY in the metadata arrays — the 5-column engine matrix never carries
  them, so labels can never leak into GFP (contract: "Labels never enter GFP").
- Raw bank/account tokens are used transiently for keys/agencies and are then dropped;
  nothing on GfpEdgeSet can reproduce them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from fractions import Fraction

import numpy as np
import pandas as pd

from lib.aml_fraud import demo_agency_index
from lib.aml_mapping import ibm_account_key
from lib.gfp.boundaries import FoldAssignment
from lib.gfp.config import CANONICAL_EDGE_COLUMNS, GfpBenchmarkConfig
from lib.gfp.folds import assign_timestamp_cohort_folds, fold_assignment_record

# IBM AML-Data column headers (mirrors scripts/lib/aml_fraud.py's private pins; the GFP
# package keeps its own named copies rather than importing another module's privates).
_COL_TIMESTAMP = "Timestamp"
_COL_FROM_BANK = "From Bank"
_COL_FROM_ACCOUNT = "Account"
_COL_TO_BANK = "To Bank"
_COL_TO_ACCOUNT = "Account.1"
_COL_AMOUNT_PAID = "Amount Paid"
_COL_PAYMENT_CURRENCY = "Payment Currency"
_COL_IS_LAUNDERING = "Is Laundering"
_REQUIRED_COLUMNS: tuple[str, ...] = (
    _COL_TIMESTAMP,
    _COL_FROM_BANK,
    _COL_FROM_ACCOUNT,
    _COL_TO_BANK,
    _COL_TO_ACCOUNT,
    _COL_AMOUNT_PAID,
    _COL_PAYMENT_CURRENCY,
    _COL_IS_LAUNDERING,
)

# float64 loses integer exactness above 2^53; the contract pins every wire value below it.
_MAX_SAFE_FLOAT_INT = float(1 << 53)
_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")
_MATRIX_DIMENSIONS = 2  # the wire format is a 2-D (rows x columns) array


@dataclass(frozen=True)
class GfpEdgeSet:
    """One dataset's built GFP edges: the engine matrix + parallel PHI-free metadata."""

    gfp_matrix: np.ndarray  # (n, 5) float64 in CANONICAL_EDGE_COLUMNS order
    labels: np.ndarray  # (n,) int8 public illicit labels — NEVER handed to the engine
    source_agency: np.ndarray  # (n,) int16 research partition of the SOURCE node (edge owner)
    dest_agency: np.ndarray  # (n,) int16 research partition of the destination node
    folds: np.ndarray  # (n,) int8 fold ids (0=train, 1=calibration, 2=holdout)
    is_target: np.ndarray  # (n,) bool — True when the row is a training example
    original_row_id: np.ndarray  # (n,) int64 position in the servable input frame
    node_count: int  # dense node-id space size
    fold_assignment: FoldAssignment  # frozen provenance record of the fold split

    def __post_init__(self) -> None:
        """Assert the 1:1 row alignment across the matrix and every metadata array."""
        n = self.gfp_matrix.shape[0]
        aligned = (
            self.labels.shape[0]
            == self.source_agency.shape[0]
            == self.dest_agency.shape[0]
            == self.folds.shape[0]
            == self.is_target.shape[0]
            == self.original_row_id.shape[0]
            == n
        )
        if not aligned:
            raise ValueError("edge metadata arrays must align 1:1 with the edge matrix")


def _usd_rate_lookup(config: GfpBenchmarkConfig) -> dict[str, float]:
    """Materialize the frozen currency pins as float factors keyed by lower-cased name."""
    return {name: float(Decimal(rate)) for name, rate in config.usd_rates.items()}


def _usd_amounts(frame: pd.DataFrame, config: GfpBenchmarkConfig) -> np.ndarray:
    """Convert raw amounts to USD via the frozen pins; unknown currencies fail loudly."""
    rates = _usd_rate_lookup(config)
    currencies = [str(value).strip().lower() for value in frame[_COL_PAYMENT_CURRENCY]]
    unknown = sorted({name for name in currencies if name not in rates})
    if unknown:
        raise ValueError(
            f"unknown currencies {unknown} — add explicit usd_rates pins; the benchmark "
            "never silently treats an unknown currency as USD"
        )
    raw = pd.to_numeric(frame[_COL_AMOUNT_PAID], errors="raise").to_numpy(dtype=np.float64)
    factors = np.array([rates[name] for name in currencies], dtype=np.float64)
    converted: np.ndarray = raw * factors
    return converted


def _epoch_seconds(frame: pd.DataFrame) -> np.ndarray:
    """Parse IBM timestamps to exact UTC epoch seconds (int64), rejecting bad values.

    The timedelta division is datetime-resolution-agnostic (pandas may parse these
    strings as microsecond datetimes), mirroring `lib.aml_fraud._occurred_seconds`.
    """
    parsed = pd.to_datetime(frame[_COL_TIMESTAMP], utc=True, errors="raise")
    if parsed.isna().any():
        raise ValueError("IBM frame carries unparseable timestamps")
    return np.asarray((parsed - _EPOCH) // pd.Timedelta(seconds=1), dtype=np.int64)


def _dense_node_ids(
    source_keys: list[str], dest_keys: list[str], order: np.ndarray
) -> tuple[np.ndarray, np.ndarray, int]:
    """Assign dense first-appearance node ids scanning src-then-dst in sorted edge order."""
    assignments: dict[str, int] = {}
    for row in order:
        for key in (source_keys[int(row)], dest_keys[int(row)]):
            if key not in assignments:
                assignments[key] = len(assignments)
    src = np.array([assignments[key] for key in source_keys], dtype=np.int64)
    dst = np.array([assignments[key] for key in dest_keys], dtype=np.int64)
    return src, dst, len(assignments)


def _validate_matrix(matrix: np.ndarray) -> None:
    """Assert wire-schema invariants: shape, finiteness, magnitude, unique edge ids."""
    if matrix.ndim != _MATRIX_DIMENSIONS or matrix.shape[1] != len(CANONICAL_EDGE_COLUMNS):
        raise ValueError(f"edge matrix must be (n, {len(CANONICAL_EDGE_COLUMNS)}) float64")
    if not np.isfinite(matrix).all():
        raise ValueError("edge matrix carries non-finite values")
    if np.abs(matrix).max(initial=0.0) >= _MAX_SAFE_FLOAT_INT:
        raise ValueError("edge matrix values must stay below 2^53 (float64 exactness)")
    edge_ids = matrix[:, 0]
    if np.unique(edge_ids).shape[0] != edge_ids.shape[0]:
        raise ValueError("edge ids must be unique")


def build_gfp_edge_set(
    frame: pd.DataFrame, config: GfpBenchmarkConfig, *, agency_count: int
) -> GfpEdgeSet:
    """Build the validated, fold-assigned GfpEdgeSet from a servable-normalized IBM frame.

    The input frame must already be servable-normalized (`lib.aml_fraud.servable_frame`)
    so GFP context equals what the ingest boundary could actually serve. The frame itself
    is never mutated; every target defaults to True (full-context datasets) — node-induced
    datasets narrow it later via `with_targets`.
    """
    missing = [column for column in _REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"IBM frame is missing required columns: {missing}")
    n = len(frame)
    if n == 0:
        raise ValueError("cannot build edges from an empty frame")

    seconds = _epoch_seconds(frame)
    original_row_id = np.arange(n, dtype=np.int64)
    order = np.lexsort((original_row_id, seconds))

    source_keys = [
        ibm_account_key(str(bank), str(account))
        for bank, account in zip(frame[_COL_FROM_BANK], frame[_COL_FROM_ACCOUNT], strict=True)
    ]
    dest_keys = [
        ibm_account_key(str(bank), str(account))
        for bank, account in zip(frame[_COL_TO_BANK], frame[_COL_TO_ACCOUNT], strict=True)
    ]
    src_ids, dst_ids, node_count = _dense_node_ids(source_keys, dest_keys, order)
    usd = _usd_amounts(frame, config)
    labels = frame[_COL_IS_LAUNDERING].astype(int).to_numpy(dtype=np.int8)
    source_agency = np.array(
        [demo_agency_index(str(bank), agency_count) for bank in frame[_COL_FROM_BANK]],
        dtype=np.int16,
    )
    dest_agency = np.array(
        [demo_agency_index(str(bank), agency_count) for bank in frame[_COL_TO_BANK]],
        dtype=np.int16,
    )

    sorted_seconds = seconds[order]
    matrix = np.column_stack(
        [
            np.arange(n, dtype=np.float64),  # edge_id: dense, in sorted order
            src_ids[order].astype(np.float64),
            dst_ids[order].astype(np.float64),
            sorted_seconds.astype(np.float64),
            usd[order],
        ]
    )
    _validate_matrix(matrix)

    fractions = (
        Fraction(config.folds.train),
        Fraction(config.folds.calibration),
        Fraction(config.folds.holdout),
    )
    folds = assign_timestamp_cohort_folds(sorted_seconds, fractions)
    sorted_labels = labels[order]
    record = fold_assignment_record(
        sorted_seconds,
        sorted_labels,
        folds,
        (config.folds.train, config.folds.calibration, config.folds.holdout),
    )
    return GfpEdgeSet(
        gfp_matrix=matrix,
        labels=sorted_labels,
        source_agency=source_agency[order],
        dest_agency=dest_agency[order],
        folds=folds,
        is_target=np.ones(n, dtype=bool),
        original_row_id=original_row_id[order],
        node_count=node_count,
        fold_assignment=record,
    )


def with_targets(edge_set: GfpEdgeSet, is_target: np.ndarray) -> GfpEdgeSet:
    """Return a copy of the edge set carrying a new stratified target mask."""
    mask = np.asarray(is_target, dtype=bool)
    if mask.shape[0] != edge_set.gfp_matrix.shape[0]:
        raise ValueError("target mask must align 1:1 with the edge matrix")
    return replace(edge_set, is_target=mask)

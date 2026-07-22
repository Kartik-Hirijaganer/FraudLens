"""GFP reference-engine known-answer tests (GFP plan Phase 4). Every expected value here
was captured from IBM snapml 1.17.2 running the committed protocol pins inside the
x86-64 container (probes 1-3), so these tests simultaneously pin the reference engine's
semantics AND serve as the fixed known answers the fake must fail and the snapml adapter
must reproduce: fan/degree instance counting (equal-time double counts included),
scatter-gather membership without per-middle ordering, temporal/simple cycle closing
rules, overflow buckets, exclusive-left windows, cross-batch state without retroactive
updates, and window-free population-moment vertex statistics."""

from __future__ import annotations

import numpy as np
import pytest

from lib.gfp.config import load_gfp_benchmark_config
from lib.gfp.reference import ReferenceGraphPreprocessor, population_moments
from lib.gfp.schema import GraphFeatureConfig

_CONFIG = GraphFeatureConfig.from_benchmark(load_gfp_benchmark_config())

# Engineered block offsets for the committed pins (empirically identical to snapml).
FAN_IN, FAN_OUT, DEG_IN, DEG_OUT = 0, 29, 58, 87
SG, TC, LC, VS = 116, 145, 174, 183
# Vertex-stat block layout: fan, degree, ratio, ts(avg,sum,var,skew,kurt), usd(avg,...).
SRC_OUT, SRC_IN, DST_OUT, DST_IN = VS, VS + 13, VS + 26, VS + 39


def _engine() -> ReferenceGraphPreprocessor:
    return ReferenceGraphPreprocessor(_CONFIG)


def _edges(rows: list[list[float]]) -> np.ndarray:
    return np.asarray(rows, dtype=np.float64)


def _nonzero(row: np.ndarray) -> dict[int, float]:
    return {int(j): round(float(v), 6) for j, v in enumerate(row) if v != 0}


# Probe case A: fan-in of node 99 from three distinct sources + fan-out of node 10.
_CASE_A = _edges(
    [
        [0, 10, 99, 1000, 5.0],
        [1, 11, 99, 1100, 6.0],
        [2, 12, 99, 1200, 7.0],
        [3, 10, 20, 1300, 8.0],
        [4, 10, 21, 1400, 9.0],
    ]
)


def test_case_a_fan_degree_histograms_match_snapml() -> None:
    out = _engine().transform_batch(_CASE_A)
    hist = [{k: v for k, v in _nonzero(row).items() if k < VS} for row in out]
    assert hist[0] == {
        FAN_IN: 1.0,
        FAN_IN + 1: 1.0,
        FAN_OUT: 1.0,
        FAN_OUT + 1: 1.0,
        DEG_IN: 1.0,
        DEG_IN + 1: 1.0,
        DEG_OUT: 1.0,
        DEG_OUT + 1: 1.0,
    }
    assert hist[1] == {FAN_IN: 1.0, FAN_IN + 1: 1.0, DEG_IN: 1.0, DEG_IN + 1: 1.0}
    assert hist[2] == {FAN_IN + 1: 1.0, DEG_IN + 1: 1.0}
    assert hist[3] == {FAN_OUT: 1.0, FAN_OUT + 1: 1.0, DEG_OUT: 1.0, DEG_OUT + 1: 1.0}
    assert hist[4] == {FAN_OUT + 1: 1.0, DEG_OUT + 1: 1.0}


def test_case_a_vertex_stats_match_snapml_population_moments() -> None:
    out = _engine().transform_batch(_CASE_A)
    row0 = out[0]
    # source_out of node 10 over ALL its edges (no window): times 1000/1300/1400.
    assert row0[SRC_OUT] == 3.0  # fan (distinct destinations)
    assert row0[SRC_OUT + 1] == 3.0  # degree
    assert row0[SRC_OUT + 2] == 1.0  # ratio
    assert row0[SRC_OUT + 3] == pytest.approx(1233.333333, abs=1e-6)  # ts avg
    assert row0[SRC_OUT + 4] == pytest.approx(3700.0)  # ts sum
    assert row0[SRC_OUT + 5] == pytest.approx(28888.888889, abs=1e-6)  # ts var
    assert row0[SRC_OUT + 6] == pytest.approx(-0.528005, abs=1e-6)  # ts skew
    assert row0[SRC_OUT + 7] == pytest.approx(1.5)  # ts kurtosis (plain, not excess)
    assert row0[SRC_OUT + 8] == pytest.approx(7.333333, abs=1e-6)  # usd avg
    assert row0[SRC_OUT + 9] == pytest.approx(22.0)  # usd sum
    # dest_in of node 99: times 1000/1100/1200, amounts 5/6/7.
    assert row0[DST_IN] == 3.0
    assert row0[DST_IN + 3] == pytest.approx(1100.0)
    assert row0[DST_IN + 4] == pytest.approx(3300.0)
    assert row0[DST_IN + 5] == pytest.approx(6666.666667, abs=1e-6)
    assert row0[DST_IN + 6] == pytest.approx(0.0)  # symmetric -> zero skew
    assert row0[DST_IN + 7] == pytest.approx(1.5)
    # node 10 has no in-edges and node 99 has no out-edges: those blocks stay zero.
    assert np.all(row0[SRC_IN : SRC_IN + 13] == 0)
    assert np.all(row0[DST_OUT : DST_OUT + 13] == 0)


def test_case_a_two_batches_state_flows_without_retroactive_updates() -> None:
    engine = _engine()
    first = engine.transform_batch(_CASE_A[:2])
    second = engine.transform_batch(_CASE_A[2:])
    # Batch 1: only the size-2 instances exist yet.
    assert {k: v for k, v in _nonzero(first[0]).items() if k < VS} == {FAN_IN: 1.0, DEG_IN: 1.0}
    assert {k: v for k, v in _nonzero(first[1]).items() if k < VS} == {FAN_IN: 1.0, DEG_IN: 1.0}
    # Batch 2: e2 completes the size-3 fan using PRIOR-batch members (rows not updated).
    assert {k: v for k, v in _nonzero(second[0]).items() if k < VS} == {
        FAN_IN + 1: 1.0,
        DEG_IN + 1: 1.0,
    }
    # Vertex stats are batch-end snapshots: batch 1 saw two in-edges of node 99.
    assert first[0][DST_IN] == 2.0
    assert first[0][DST_IN + 3] == pytest.approx(1050.0)
    assert second[0][DST_IN] == 3.0  # batch 2 sees the accumulated three


# Probe case B: temporal + simple cycle 1 -> 2 -> 3 -> 1, then an out-of-window edge.
_CASE_B = _edges(
    [
        [0, 1, 2, 100, 10.0],
        [1, 2, 3, 200, 11.0],
        [2, 3, 1, 300, 12.0],
        [3, 4, 5, 200000, 13.0],
    ]
)


def test_case_b_cycles_counted_once_at_the_closing_edge() -> None:
    out = _engine().transform_batch(_CASE_B)
    for row in out[:3]:
        hist = {k: v for k, v in _nonzero(row).items() if k < VS}
        assert hist == {TC + 1: 1.0, LC + 1: 1.0}  # one length-3 temporal + simple cycle
    assert {k: v for k, v in _nonzero(out[3]).items() if k < VS} == {}


# Probe case C: self-loop, repeated edge, equal timestamps.
_CASE_C = _edges(
    [
        [0, 7, 7, 500, 1.0],
        [1, 8, 9, 600, 2.0],
        [2, 8, 9, 600, 3.0],
        [3, 9, 8, 600, 4.0],
    ]
)


def test_case_c_equal_timestamps_self_loops_and_repeated_edges() -> None:
    out = _engine().transform_batch(_CASE_C)
    assert {k: v for k, v in _nonzero(out[0]).items() if k < VS} == {}  # self-loop: nothing
    # Equal-time repeated edges double-count the size-2 degree instance (probed).
    assert {k: v for k, v in _nonzero(out[1]).items() if k < VS} == {
        DEG_IN: 2.0,
        DEG_OUT: 2.0,
        LC: 1.0,
    }
    assert {k: v for k, v in _nonzero(out[2]).items() if k < VS} == {
        DEG_IN: 2.0,
        DEG_OUT: 2.0,
        LC: 1.0,
    }
    # e3 closes TWO length-2 simple cycles (one per parallel edge); equal times mean
    # NO temporal cycle (strictly increasing required).
    row3 = {k: v for k, v in _nonzero(out[3]).items() if k < VS}
    assert row3 == {LC: 2.0}
    # Self-loop vertex stats: the loop edge appears in ALL FOUR endpoint blocks.
    assert out[0][SRC_OUT] == 1.0 and out[0][SRC_IN] == 1.0
    assert out[0][DST_OUT] == 1.0 and out[0][DST_IN] == 1.0


# Probe D1: scatter-gather a=1 -> {10, 11} -> b=2 with a non-gathering middle 12.
_CASE_D1 = _edges(
    [
        [0, 1, 10, 100, 1.0],
        [1, 1, 11, 200, 2.0],
        [2, 1, 12, 150, 5.0],
        [3, 10, 2, 300, 3.0],
        [4, 11, 2, 400, 4.0],
    ]
)


def test_case_d1_scatter_gather_members_exclude_non_gathering_middles() -> None:
    out = _engine().transform_batch(_CASE_D1)
    sg_cells = [{k: v for k, v in _nonzero(row).items() if SG <= k < TC} for row in out]
    assert sg_cells[0] == {SG: 1.0}
    assert sg_cells[1] == {SG: 1.0}
    assert sg_cells[2] == {}  # middle 12 never gathers: its scatter edge is no member
    assert sg_cells[3] == {SG: 1.0}
    assert sg_cells[4] == {SG: 1.0}


# Probe E3: the same pattern completed by a LATE scatter (no per-middle ordering).
_CASE_E3 = _edges(
    [
        [0, 10, 2, 100, 1.0],
        [1, 11, 2, 150, 2.0],
        [2, 1, 10, 200, 3.0],
        [3, 1, 11, 250, 4.0],
    ]
)


def test_case_e3_scatter_after_gather_still_counts() -> None:
    out = _engine().transform_batch(_CASE_E3)
    for row in out:
        assert {k: v for k, v in _nonzero(row).items() if SG <= k < TC} == {SG: 1.0}


# Probe D3: 31 distinct sources overflow the ge_30 bucket.
_CASE_D3 = _edges([[i, 700 + i, 600, 1000 + i, 1.0] for i in range(31)])


def test_case_d3_overflow_sizes_accumulate_in_the_last_cell() -> None:
    out = _engine().transform_batch(_CASE_D3)
    first = {k: v for k, v in _nonzero(out[0]).items() if k < DEG_IN}
    assert first == {**{FAN_IN + c: 1.0 for c in range(28)}, FAN_IN + 28: 2.0}
    last = {k: v for k, v in _nonzero(out[30]).items() if k < DEG_IN}
    assert last == {FAN_IN + 28: 1.0}  # the 31st edge only joins the size-31 instance


# Probe D4/D4b: the window is (t - tw, t] — the exact-boundary edge is EXCLUDED.
def test_case_d4_window_left_boundary_is_exclusive() -> None:
    at_boundary = _engine().transform_batch(
        _edges([[0, 1, 2, 1000, 1.0], [1, 3, 2, 1000 + 86400, 2.0]])
    )
    for row in at_boundary:
        assert {k: v for k, v in _nonzero(row).items() if k < VS} == {}
    inside = _engine().transform_batch(_edges([[0, 1, 2, 1000, 1.0], [1, 3, 2, 1000 + 86399, 2.0]]))
    for row in inside:
        assert {k: v for k, v in _nonzero(row).items() if k < VS} == {FAN_IN: 1.0, DEG_IN: 1.0}


# Probe D5: a two-edge cycle with strictly increasing times is temporal AND simple.
def test_case_d5_two_cycle_counts_in_both_families() -> None:
    out = _engine().transform_batch(_edges([[0, 5, 6, 100, 1.0], [1, 6, 5, 200, 2.0]]))
    for row in out:
        assert {k: v for k, v in _nonzero(row).items() if k < VS} == {TC: 1.0, LC: 1.0}


# Probes E1/E4: vertex stats ignore every window; fan histograms do not.
def test_case_e1_e4_vertex_stats_are_window_free_but_histograms_are_windowed() -> None:
    engine = _engine()
    engine.transform_batch(_edges([[0, 40, 50, 1000, 1.0]]))
    second = engine.transform_batch(_edges([[1, 41, 50, 200000, 2.0]]))
    row = second[0]
    assert {k: v for k, v in _nonzero(row).items() if k < VS} == {}  # fan window excludes
    assert row[DST_IN] == 2.0  # ...but vertex stats aggregate the whole node history
    assert row[DST_IN + 3] == pytest.approx(100500.0)
    assert row[DST_IN + 5] == pytest.approx(9900250000.0)


def test_empty_and_single_edge_batches() -> None:
    engine = _engine()
    empty = engine.transform_batch(np.empty((0, 5), dtype=np.float64))
    assert empty.shape == (0, len(engine.feature_names))
    single = engine.transform_batch(_edges([[0, 1, 2, 100, 1.0]]))
    assert {k: v for k, v in _nonzero(single[0]).items() if k < VS} == {}
    assert single[0][SRC_OUT] == 1.0 and single[0][DST_IN] == 1.0


def test_transform_never_mutates_the_caller_batch() -> None:
    batch = _CASE_A.copy()
    _engine().transform_batch(batch)
    assert np.array_equal(batch, _CASE_A)


def test_population_moments_fallbacks() -> None:
    assert population_moments([]) == (0.0, 0.0, 0.0, 0.0, 0.0)
    assert population_moments([4.0]) == (4.0, 4.0, 0.0, 0.0, 0.0)
    avg, total, var, skew, kurt = population_moments([2.0, 3.0])
    assert (avg, total, var) == (2.5, 5.0, 0.25)
    assert skew == pytest.approx(0.0)
    assert kurt == pytest.approx(1.0)  # plain kurtosis of a symmetric pair (probed)

"""Summary: Pure-Python reference GFP engine — the benchmark's readable oracle (GFP plan
Phase 4). It reproduces the EMPIRICALLY PINNED snapml 1.17.2 batch semantics, verified
against containerized probes: (1) a transform pre-inserts the whole batch into the
accumulated graph, so equal-time and later-in-batch edges are visible to every arrival's
window `(t - tw, t]` (exclusive left, inclusive right); (2) each arriving edge anchors one
pattern instance per family (fan/degree per endpoint, scatter-gather per (a,b) pair) whose
SIZE is binned and whose MEMBER edges in the current batch each gain +1 — prior-batch
members influence sizes but their rows are never retro-updated; (3) cycles are counted
once, at their last-processed member edge (temporal cycles need strictly increasing
times); (4) vertex statistics aggregate over ALL accumulated edges of a node with NO
window, using population moments (plain, non-excess kurtosis). Built for clarity and
small/medium smoke graphs — never full-data performance.

Key classes:
- ReferenceGraphPreprocessor: stateful pure engine satisfying the GraphPreprocessor protocol.

Key functions:
- population_moments: (mean, sum, variance, skew, kurtosis) with snapml's 0-fallbacks.

Notes:
- Overflow semantics: histogram cell i covers [lo+i, lo+i+1); every size >= hi lands in
  the last (`ge_<hi>`) cell — exactly what snapml produced for k = 30 and 31.
- Scatter-gather: middles are distinct vertices m with a->m and m->b inside the window;
  m never equals a or b; NO per-middle scatter-before-gather ordering (probed: a pattern
  completed by a late scatter edge still counts).
- Complexity is deliberately naive (linear scans + bounded DFS); the snapml adapter is
  the full-data engine and Phase-8 parity ties the two together per feature.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from lib.gfp.protocol import validate_edge_batch
from lib.gfp.schema import GraphFeatureConfig, GraphFeatureSchema

_STRUCTURAL_STATS = ("fan", "degree", "ratio")
# Canonical raw-column positions in the wire schema (utc_epoch_s=3, usd_amount=4).
_RAW_COLUMN_INDEX = {"utc_epoch_s": 3, "usd_amount": 4}


def population_moments(values: list[float]) -> tuple[float, float, float, float, float]:
    """Return (avg, sum, variance, skew, kurtosis) as population moments, snapml-style.

    Skew is m3 / m2^1.5 and kurtosis is PLAIN m4 / m2^2 (not excess); both fall back to
    0.0 when the variance is zero (probed single-element and constant sets).
    """
    if not values:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    array = np.asarray(values, dtype=np.float64)
    mean = float(array.mean())
    total = float(array.sum())
    deviations = array - mean
    m2 = float(np.mean(deviations**2))
    if m2 == 0.0:
        return mean, total, 0.0, 0.0, 0.0
    m3 = float(np.mean(deviations**3))
    m4 = float(np.mean(deviations**4))
    return mean, total, m2, m3 / m2**1.5, m4 / m2**2


class ReferenceGraphPreprocessor:
    """Stateful pure engine reproducing the pinned snapml batch semantics on small graphs."""

    def __init__(self, config: GraphFeatureConfig) -> None:
        """Freeze the schema and start with an empty accumulated graph."""
        self._config = config
        self._schema = GraphFeatureSchema.from_config(config)
        self._offsets = self._family_offsets()
        self._src: list[int] = []
        self._dst: list[int] = []
        self._time: list[float] = []
        self._raw: list[dict[str, float]] = []
        self._out_by_node: dict[int, list[int]] = defaultdict(list)
        self._in_by_node: dict[int, list[int]] = defaultdict(list)

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Engineered names in emission order (delegates to the derived schema)."""
        return self._schema.feature_names

    @property
    def schema(self) -> GraphFeatureSchema:
        """The derived schema (family slices reused by parity tests)."""
        return self._schema

    def _family_offsets(self) -> dict[str, int]:
        """Column offset of each family block inside the engineered output."""
        offsets: dict[str, int] = {}
        cursor = 0
        for family in (
            "fan_in",
            "fan_out",
            "degree_in",
            "degree_out",
            "scatter_gather",
            "temporal_cycle",
            "simple_cycle",
            "vertex_stats",
        ):
            offsets[family] = cursor
            cursor += len(self._schema.family_names[family])
        return offsets

    # ------------------------------------------------------------------ state helpers
    def _window(self, indices: list[int], t: float, tw: int) -> list[int]:
        """State indices among `indices` whose time lies in (t - tw, t]."""
        return [i for i in indices if t - tw < self._time[i] <= t]

    def _cell(self, size: int, lo: int, hi: int) -> int | None:
        """Histogram cell for a pattern size (None below lo; >= hi overflows into last)."""
        if size < lo:
            return None
        return min(size, hi) - lo

    def _bump(
        self,
        out: np.ndarray,
        members: list[int],
        first_batch_index: int,
        family: str,
        cell: int,
    ) -> None:
        """Add one pattern instance to every member row that belongs to the current batch."""
        offset = self._offsets[family] + cell
        for member in members:
            if member >= first_batch_index:
                out[member - first_batch_index, offset] += 1.0

    # ------------------------------------------------------------------ pattern families
    def _fan_and_degree(self, i: int, out: np.ndarray, first_batch_index: int) -> None:
        """Fan (distinct counterparties) and degree (edge count) instances anchored at i."""
        t = self._time[i]
        specs = (
            ("fan_in", self._in_by_node[self._dst[i]], self._config.fan_bins, "src"),
            ("fan_out", self._out_by_node[self._src[i]], self._config.fan_bins, "dst"),
            ("degree_in", self._in_by_node[self._dst[i]], self._config.degree_bins, None),
            ("degree_out", self._out_by_node[self._src[i]], self._config.degree_bins, None),
        )
        windows = {
            "fan_in": self._config.windows.fan_window_s,
            "fan_out": self._config.windows.fan_window_s,
            "degree_in": self._config.windows.degree_window_s,
            "degree_out": self._config.windows.degree_window_s,
        }
        for family, adjacency, bins, distinct_end in specs:
            members = self._window(adjacency, t, windows[family])
            if distinct_end == "src":
                size = len({self._src[m] for m in members})
            elif distinct_end == "dst":
                size = len({self._dst[m] for m in members})
            else:
                size = len(members)
            cell = self._cell(size, bins.lo, bins.hi)
            if cell is not None:
                self._bump(out, members, first_batch_index, family, cell)

    def _scatter_gather(self, i: int, out: np.ndarray, first_batch_index: int) -> None:
        """Scatter-gather instances the arrival at i completes (per distinct (a, b) pair)."""
        t = self._time[i]
        tw = self._config.windows.scatter_gather_window_s
        bins = self._config.scatter_gather_bins
        u, v = self._src[i], self._dst[i]
        pairs: set[tuple[int, int]] = set()
        # Arrival as a GATHER edge (middle=u, sink=v): every scatter source into u.
        for e in self._window(self._in_by_node[u], t, tw):
            pairs.add((self._src[e], v))
        # Arrival as a SCATTER edge (fan-out of u into middle=v): every sink v reaches.
        for e in self._window(self._out_by_node[v], t, tw):
            pairs.add((u, self._dst[e]))
        for a, b in pairs:
            if a == b:
                continue
            scatter_edges = self._window(self._out_by_node[a], t, tw)
            gather_edges = self._window(self._in_by_node[b], t, tw)
            scattered_to = {self._dst[e] for e in scatter_edges} - {a, b}
            gathered_from = {self._src[e] for e in gather_edges} - {a, b}
            middles = scattered_to & gathered_from
            cell = self._cell(len(middles), bins.lo, bins.hi)
            if cell is None:
                continue
            members = [e for e in scatter_edges if self._dst[e] in middles]
            members += [e for e in gather_edges if self._src[e] in middles]
            self._bump(out, members, first_batch_index, "scatter_gather", cell)

    def _cycles_from(self, i: int, *, temporal: bool) -> list[list[int]]:
        """Vertex-simple directed cycles CLOSED by edge i (i is the last member).

        Temporal cycles need strictly increasing times ending strictly before t_i;
        simple cycles accept any in-window order but only members processed before i,
        with total length capped at the configured maximum.
        """
        u, v = self._src[i], self._dst[i]
        if u == v:
            return []
        t = self._time[i]
        if temporal:
            tw = self._config.windows.temporal_cycle_window_s
            max_len = self._config.temporal_cycle_bins.hi + 1  # DFS guard; sizes overflow
        else:
            tw = self._config.windows.simple_cycle_window_s
            max_len = self._config.simple_cycle_max_length
        cycles: list[list[int]] = []

        def walk(node: int, visited: set[int], path: list[int], last_time: float) -> None:
            if len(path) >= max_len:  # path + closing edge would exceed the cap
                return
            for e in self._out_by_node[node]:
                te = self._time[e]
                if not t - tw < te <= t:
                    continue
                if temporal:
                    if not (last_time < te < t):
                        continue
                elif e >= i:
                    continue  # simple cycles: only members processed before the closer
                nxt = self._dst[e]
                if nxt == u:
                    cycles.append([*path, e])
                    continue
                if nxt in visited or nxt == v:
                    continue
                walk(nxt, visited | {nxt}, [*path, e], te if temporal else last_time)

        walk(v, {u, v}, [], float("-inf"))
        return cycles

    def _cycle_instances(self, i: int, out: np.ndarray, first_batch_index: int) -> None:
        """Bin the temporal and length-constrained simple cycles closed by edge i."""
        for family, temporal, bins in (
            ("temporal_cycle", True, self._config.temporal_cycle_bins),
            ("simple_cycle", False, self._config.simple_cycle_bins),
        ):
            for path in self._cycles_from(i, temporal=temporal):
                members = [*path, i]
                cell = self._cell(len(members), bins.lo, bins.hi)
                if cell is not None:
                    self._bump(out, members, first_batch_index, family, cell)

    def _vertex_stats_block(self, node: int, direction: str) -> list[float]:
        """One endpoint block: structural stats then per-raw-column population moments."""
        edges = self._out_by_node[node] if direction == "out" else self._in_by_node[node]
        counterparties = (
            {self._dst[e] for e in edges} if direction == "out" else {self._src[e] for e in edges}
        )
        fan = float(len(counterparties))
        degree = float(len(edges))
        structural = {"fan": fan, "degree": degree, "ratio": degree / fan if fan else 0.0}
        stats_config = self._config.vertex_stats
        block = [structural[s] for s in stats_config.stats if s in _STRUCTURAL_STATS]
        moment_stats = [s for s in stats_config.stats if s not in _STRUCTURAL_STATS]
        for column in stats_config.raw_columns:
            values = [self._raw[e][column] for e in edges]
            avg, total, variance, skew, kurtosis = population_moments(values)
            named = {"avg": avg, "sum": total, "var": variance, "skew": skew, "kurtosis": kurtosis}
            block.extend(named[s] for s in moment_stats)
        return block

    def _vertex_stats(self, i: int, out: np.ndarray, first_batch_index: int) -> None:
        """Emit the four endpoint blocks for one batch row (state-wide, no window)."""
        row = i - first_batch_index
        offset = self._offsets["vertex_stats"]
        cursor = offset
        endpoint_nodes = {
            "source_out": (self._src[i], "out"),
            "source_in": (self._src[i], "in"),
            "target_out": (self._dst[i], "out"),
            "target_in": (self._dst[i], "in"),
        }
        for endpoint in self._config.vertex_stats.endpoints:
            node, direction = endpoint_nodes[endpoint]
            block = self._vertex_stats_block(node, direction)
            out[row, cursor : cursor + len(block)] = block
            cursor += len(block)

    # ------------------------------------------------------------------ protocol surface
    def transform_batch(self, edge_batch: np.ndarray) -> np.ndarray:
        """Pre-insert the batch, evaluate every arrival, and emit engineered features."""
        validate_edge_batch(edge_batch)
        n = edge_batch.shape[0]
        out = np.zeros((n, len(self._schema.feature_names)), dtype=np.float64)
        if n == 0:
            return out
        first_batch_index = len(self._time)
        raw_columns = self._config.vertex_stats.raw_columns
        for row in np.asarray(edge_batch, dtype=np.float64):
            index = len(self._time)
            self._src.append(int(row[1]))
            self._dst.append(int(row[2]))
            self._time.append(float(row[3]))
            self._raw.append({c: float(row[_RAW_COLUMN_INDEX[c]]) for c in raw_columns})
            self._out_by_node[int(row[1])].append(index)
            self._in_by_node[int(row[2])].append(index)
        for i in range(first_batch_index, first_batch_index + n):
            self._fan_and_degree(i, out, first_batch_index)
            self._scatter_gather(i, out, first_batch_index)
            self._cycle_instances(i, out, first_batch_index)
            self._vertex_stats(i, out, first_batch_index)
        return out

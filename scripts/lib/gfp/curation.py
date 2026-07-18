"""Summary: Deterministic motif curation for the research visual (GFP plan Phase 6).
From the GLOBAL graph context it selects exactly three typology exemplars — a
scatter-gather, an intra-tenant cycle, and a cross-tenant cycle (3-10 edges) — and
redacts them into opaque `CuratedMotif` records: sequential node/edge ids, RELATIVE
time offsets, coarse USD amount BANDS, and agency indices only (never raw tokens,
timestamps, or amounts). Candidates are reconstructed from window-bounded local
searches anchored at edges whose GLOBAL GFP pattern features are non-zero, then ranked
by the plan's deterministic key: contains a public illicit edge -> largest
global-vs-tenant feature delta -> fewest nodes (<=12) -> earliest timestamp -> stable
edge-id hash. A typology with no candidate is reported MISSING — never invented — and
publication fails on a missing cross-tenant cycle.

Key classes:
- CurationSignals: per-edge global pattern activity + global-vs-tenant feature delta.
- CurationResult: the selected motifs plus any typologies with no candidate.

Key functions:
- curation_signals: reduce the global/per-tenant feature matrices to per-edge signals.
- curate_motifs: select + redact the three typology exemplars from one edge set.

Notes:
- Reconstruction mirrors the engines' windowed pattern definitions but scans the FULL
  time-windowed context (batch-independent), so exemplars are real graph patterns even
  when engine counting split them across batches; candidate pools are bounded by named
  deterministic caps so full-dataset curation stays tractable.
- `servable` is True only when EVERY motif edge is owned by one tenant; the
  cross-tenant exemplar is therefore never servable (the point of the visual).
- The public illicit label steers selection ONLY (ranking); it is never emitted on any
  curated record.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

import numpy as np

from lib.gfp.boundaries import CuratedMotif, CuratedMotifEdge, CuratedMotifNode
from lib.gfp.edges import GfpEdgeSet
from lib.gfp.schema import GraphFeatureConfig, GraphFeatureSchema

# Typology ids (also the CuratedMotif.typology literals).
Typology = Literal["scatter_gather", "intra_tenant_cycle", "cross_tenant_cycle"]
SCATTER_GATHER: Typology = "scatter_gather"
INTRA_TENANT_CYCLE: Typology = "intra_tenant_cycle"
CROSS_TENANT_CYCLE: Typology = "cross_tenant_cycle"
TYPOLOGIES: tuple[Typology, ...] = (SCATTER_GATHER, INTRA_TENANT_CYCLE, CROSS_TENANT_CYCLE)

# Plan Phase 6 display bounds: motifs stay legible at <=12 nodes; cycles carry 3-10 edges.
_MAX_MOTIF_NODES = 12
_MIN_CYCLE_EDGES = 3
# A counted scatter-gather needs >=2 distinct middle accounts (the histogram bin floor).
_MIN_SCATTER_MIDDLES = 2
# Deterministic tractability caps (documented bounded pools — selection stays a pure
# function of the dataset + config; the caps only bound how many candidates are ranked).
_MAX_CANDIDATES_PER_TYPOLOGY = 256
_MAX_CYCLES_PER_ANCHOR = 64

# Coarse USD display bands: upper bounds paired with labels; amounts >= the last bound
# take the overflow label. Bands are the ONLY amount signal that leaves this module.
_AMOUNT_BANDS: tuple[tuple[float, str], ...] = (
    (100.0, "lt-100"),
    (1_000.0, "100-1k"),
    (10_000.0, "1k-10k"),
    (100_000.0, "10k-100k"),
)
_AMOUNT_OVERFLOW_BAND = "ge-100k"
_MOTIF_ID_DIGEST_LENGTH = 12


@dataclass(frozen=True)
class CurationSignals:
    """Per-edge curation signals reduced from one dataset's scope feature matrices."""

    scatter_activity: np.ndarray  # global scatter-gather family sum per edge
    cycle_activity: np.ndarray  # global simple-cycle family sum per edge
    feature_delta: np.ndarray  # sum |global - per-tenant| across all engineered columns


def curation_signals(
    schema: GraphFeatureSchema, global_features: np.ndarray, per_tenant_features: np.ndarray
) -> CurationSignals:
    """Reduce the two scope matrices to the compact per-edge signals curation ranks on."""
    if global_features.shape != per_tenant_features.shape:
        raise ValueError("scope feature matrices must align to reduce curation signals")
    scatter_columns = list(schema.column_indices(schema.family_names["scatter_gather"]))
    cycle_columns = list(schema.column_indices(schema.family_names["simple_cycle"]))
    delta = np.abs(global_features.astype(np.float64) - per_tenant_features.astype(np.float64)).sum(
        axis=1
    )
    return CurationSignals(
        scatter_activity=global_features[:, scatter_columns].astype(np.float64).sum(axis=1),
        cycle_activity=global_features[:, cycle_columns].astype(np.float64).sum(axis=1),
        feature_delta=delta,
    )


@dataclass(frozen=True)
class CurationResult:
    """The curated exemplars plus every typology that had no real candidate."""

    motifs: tuple[CuratedMotif, ...]
    missing_typologies: tuple[str, ...]


@dataclass(frozen=True)
class _EdgeIndex:
    """Window-searchable adjacency over one edge set (per-node, time-sorted)."""

    src: np.ndarray
    dst: np.ndarray
    times: np.ndarray
    amounts: np.ndarray
    out_rows: np.ndarray  # edge rows sorted by (src, time, row)
    out_keys: np.ndarray  # src per out_rows entry (searchsorted key)
    in_rows: np.ndarray  # edge rows sorted by (dst, time, row)
    in_keys: np.ndarray  # dst per in_rows entry


def _build_edge_index(edge_set: GfpEdgeSet) -> _EdgeIndex:
    """Build the per-node time-sorted adjacency arrays once per curation run."""
    matrix = edge_set.gfp_matrix
    src = matrix[:, 1].astype(np.int64)
    dst = matrix[:, 2].astype(np.int64)
    times = matrix[:, 3].astype(np.float64)
    rows = np.arange(matrix.shape[0], dtype=np.int64)
    out_order = np.lexsort((rows, times, src))
    in_order = np.lexsort((rows, times, dst))
    return _EdgeIndex(
        src=src,
        dst=dst,
        times=times,
        amounts=matrix[:, 4].astype(np.float64),
        out_rows=rows[out_order],
        out_keys=src[out_order],
        in_rows=rows[in_order],
        in_keys=dst[in_order],
    )


def _windowed(
    index: _EdgeIndex, node: int, t: float, window_s: int, *, direction: str
) -> np.ndarray:
    """Edge rows adjacent to `node` with time in (t - window, t], time-ascending."""
    keys, rows = (
        (index.out_keys, index.out_rows) if direction == "out" else (index.in_keys, index.in_rows)
    )
    lo = int(np.searchsorted(keys, node, side="left"))
    hi = int(np.searchsorted(keys, node, side="right"))
    node_rows = rows[lo:hi]
    node_times = index.times[node_rows]
    left = int(np.searchsorted(node_times, t - window_s, side="right"))
    right = int(np.searchsorted(node_times, t, side="right"))
    return node_rows[left:right]


@dataclass(frozen=True)
class _MotifCandidate:
    """One reconstructed pattern instance, carrying its deterministic ranking key parts."""

    member_rows: tuple[int, ...]
    node_ids: tuple[int, ...]
    has_illicit: bool
    feature_delta: float
    first_time: float
    id_hash: str


def _candidate(edge_set: GfpEdgeSet, signals: CurationSignals, rows: list[int]) -> _MotifCandidate:
    """Assemble one candidate's ranking key parts from its member edge rows."""
    ordered = sorted(rows)
    matrix = edge_set.gfp_matrix
    nodes = sorted(
        {int(matrix[row, 1]) for row in ordered} | {int(matrix[row, 2]) for row in ordered}
    )
    digest = hashlib.sha256(",".join(str(row) for row in ordered).encode("utf-8")).hexdigest()
    return _MotifCandidate(
        member_rows=tuple(ordered),
        node_ids=tuple(nodes),
        has_illicit=bool(edge_set.labels[ordered].any()),
        feature_delta=float(signals.feature_delta[ordered].sum()),
        first_time=float(matrix[ordered, 3].min()),
        id_hash=digest,
    )


def _ranked(candidates: list[_MotifCandidate]) -> list[_MotifCandidate]:
    """Apply the plan's deterministic ranking (illicit, delta, nodes, time, hash)."""
    return sorted(
        candidates,
        key=lambda c: (
            not c.has_illicit,
            -c.feature_delta,
            len(c.node_ids),
            c.first_time,
            c.id_hash,
        ),
    )


def _anchor_order(
    edge_set: GfpEdgeSet, signals: CurationSignals, activity: np.ndarray
) -> np.ndarray:
    """Deterministic anchor scan order: illicit first, largest delta, earliest, lowest row."""
    anchors = np.flatnonzero(activity > 0)
    if anchors.shape[0] == 0:
        return anchors
    times = edge_set.gfp_matrix[anchors, 3]
    keys = np.lexsort(
        (anchors, times, -signals.feature_delta[anchors], 1 - edge_set.labels[anchors])
    )
    return anchors[keys]


def _scatter_gather_candidates(
    edge_set: GfpEdgeSet, index: _EdgeIndex, signals: CurationSignals, window_s: int
) -> list[_MotifCandidate]:
    """Reconstruct scatter-gather instances (a -> middles -> b) around active anchors."""
    candidates: list[_MotifCandidate] = []
    seen: set[tuple[int, ...]] = set()
    for anchor in _anchor_order(edge_set, signals, signals.scatter_activity):
        u, v = int(index.src[anchor]), int(index.dst[anchor])
        t = float(index.times[anchor])
        pairs = {(int(index.src[e]), v) for e in _windowed(index, u, t, window_s, direction="in")}
        pairs |= {(u, int(index.dst[e])) for e in _windowed(index, v, t, window_s, direction="out")}
        for a, b in sorted(pairs):
            if a == b:
                continue
            scatter = _windowed(index, a, t, window_s, direction="out")
            gather = _windowed(index, b, t, window_s, direction="in")
            excluded = {a, b}
            middles = ({int(index.dst[e]) for e in scatter} - excluded) & (
                {int(index.src[e]) for e in gather} - excluded
            )
            if len(middles) < _MIN_SCATTER_MIDDLES or len(middles) + 2 > _MAX_MOTIF_NODES:
                continue
            members = [int(e) for e in scatter if int(index.dst[e]) in middles]
            members += [int(e) for e in gather if int(index.src[e]) in middles]
            key = tuple(sorted(set(members)))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(_candidate(edge_set, signals, list(key)))
            if len(candidates) >= _MAX_CANDIDATES_PER_TYPOLOGY:
                return candidates
    return candidates


def _cycles_closed_by(
    index: _EdgeIndex, closer: int, window_s: int, max_edges: int
) -> list[list[int]]:
    """Vertex-simple directed cycles CLOSED by edge `closer` (earlier rows only)."""
    u, v = int(index.src[closer]), int(index.dst[closer])
    if u == v:
        return []
    t = float(index.times[closer])
    cycles: list[list[int]] = []

    def walk(node: int, visited: set[int], path: list[int]) -> None:
        if len(cycles) >= _MAX_CYCLES_PER_ANCHOR or len(path) + 1 >= max_edges:
            return
        for e in _windowed(index, node, t, window_s, direction="out"):
            row = int(e)
            if row >= closer:
                continue  # only members processed before the closing edge
            nxt = int(index.dst[row])
            if nxt == u:
                cycles.append([*path, row])
                if len(cycles) >= _MAX_CYCLES_PER_ANCHOR:
                    return
                continue
            if nxt in visited or nxt == v:
                continue
            walk(nxt, visited | {nxt}, [*path, row])

    walk(v, {u, v}, [])
    return [[*path, closer] for path in cycles]


def _cycle_candidates(  # noqa: PLR0913 - one search binds the graph, signals, and window pins
    edge_set: GfpEdgeSet,
    index: _EdgeIndex,
    signals: CurationSignals,
    window_s: int,
    max_edges: int,
    *,
    cross_tenant: bool,
) -> list[_MotifCandidate]:
    """Reconstruct 3-10-edge cycles around active anchors, split by tenant span."""
    candidates: list[_MotifCandidate] = []
    seen: set[tuple[int, ...]] = set()
    for anchor in _anchor_order(edge_set, signals, signals.cycle_activity):
        for members in _cycles_closed_by(index, int(anchor), window_s, max_edges):
            if not _MIN_CYCLE_EDGES <= len(members) <= max_edges:
                continue
            owners = {int(edge_set.source_agency[row]) for row in members}
            if (len(owners) > 1) != cross_tenant:
                continue
            key = tuple(sorted(members))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(_candidate(edge_set, signals, list(key)))
            if len(candidates) >= _MAX_CANDIDATES_PER_TYPOLOGY:
                return candidates
    return candidates


def _amount_band(amount: float) -> str:
    """Map a USD amount onto its coarse display band (never the raw value)."""
    for upper, label in _AMOUNT_BANDS:
        if amount < upper:
            return label
    return _AMOUNT_OVERFLOW_BAND


def _node_agencies(edge_set: GfpEdgeSet) -> dict[int, int]:
    """Map each dense node id to its owning demo agency (consistent by construction)."""
    matrix = edge_set.gfp_matrix
    agencies: dict[int, int] = {}
    for column, agency_array in ((1, edge_set.source_agency), (2, edge_set.dest_agency)):
        for node, agency in zip(matrix[:, column].astype(np.int64), agency_array, strict=True):
            existing = agencies.setdefault(int(node), int(agency))
            if existing != int(agency):
                raise ValueError("node ownership is inconsistent across edges")
    return agencies


def _redact(
    edge_set: GfpEdgeSet,
    candidate: _MotifCandidate,
    typology: Typology,
    node_agencies: dict[int, int],
) -> CuratedMotif:
    """Redact one candidate into the opaque CuratedMotif the research page renders."""
    matrix = edge_set.gfp_matrix
    ordered_rows = sorted(candidate.member_rows, key=lambda row: (matrix[row, 3], row))
    first_time = float(matrix[ordered_rows[0], 3])
    node_ids: dict[int, str] = {}
    for row in ordered_rows:
        for node in (int(matrix[row, 1]), int(matrix[row, 2])):
            if node not in node_ids:
                node_ids[node] = f"node-{len(node_ids) + 1:02d}"
    nodes = tuple(
        CuratedMotifNode(node_id=opaque, agency_index=node_agencies[node])
        for node, opaque in node_ids.items()
    )
    edges = tuple(
        CuratedMotifEdge(
            edge_id=f"edge-{position + 1:02d}",
            source_node_id=node_ids[int(matrix[row, 1])],
            target_node_id=node_ids[int(matrix[row, 2])],
            time_offset_s=int(matrix[row, 3] - first_time),
            amount_band=_amount_band(float(matrix[row, 4])),
            owner_agency_index=int(edge_set.source_agency[row]),
        )
        for position, row in enumerate(ordered_rows)
    )
    owners = {edge.owner_agency_index for edge in edges}
    return CuratedMotif(
        motif_id=f"{typology}-{candidate.id_hash[:_MOTIF_ID_DIGEST_LENGTH]}",
        typology=typology,
        nodes=nodes,
        edges=edges,
        servable=len(owners) == 1,
    )


def curate_motifs(
    edge_set: GfpEdgeSet, config: GraphFeatureConfig, signals: CurationSignals
) -> CurationResult:
    """Select + redact the three typology exemplars from the GLOBAL context edge set.

    A typology with no real candidate is reported missing — a motif is never invented
    (plan Phase 6: publication fails when the cross-tenant cycle is absent).
    """
    n = edge_set.gfp_matrix.shape[0]
    if not (
        signals.scatter_activity.shape[0]
        == signals.cycle_activity.shape[0]
        == signals.feature_delta.shape[0]
        == n
    ):
        raise ValueError("curation signals must align 1:1 with the edge set")
    index = _build_edge_index(edge_set)
    node_agencies = _node_agencies(edge_set)
    cycle_window = config.windows.simple_cycle_window_s
    max_cycle_edges = config.simple_cycle_max_length
    pools: dict[str, list[_MotifCandidate]] = {
        SCATTER_GATHER: _scatter_gather_candidates(
            edge_set, index, signals, config.windows.scatter_gather_window_s
        ),
        INTRA_TENANT_CYCLE: _cycle_candidates(
            edge_set, index, signals, cycle_window, max_cycle_edges, cross_tenant=False
        ),
        CROSS_TENANT_CYCLE: _cycle_candidates(
            edge_set, index, signals, cycle_window, max_cycle_edges, cross_tenant=True
        ),
    }
    motifs: list[CuratedMotif] = []
    missing: list[str] = []
    for typology in TYPOLOGIES:
        ranked = _ranked(pools[typology])
        if not ranked:
            missing.append(typology)
            continue
        motifs.append(_redact(edge_set, ranked[0], typology, node_agencies))
    return CurationResult(motifs=tuple(motifs), missing_typologies=tuple(missing))

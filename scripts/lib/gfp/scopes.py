"""Summary: Global + per-tenant edge streams for the offline GFP benchmark (GFP plan
Phase 3). The GLOBAL stream is every context edge; the PER-TENANT stream for agency N is
only the edges N owns (ownership = the SOURCE node's demo agency, matching
`map_ibm_demo_row` and the demo-ingest repository selection — counterparty destination
nodes still appear as endpoints inside a tenant's edges, but other agencies' EDGES never
do). Per-tenant features computed per stream are concatenated back into original edge
order, and validation asserts the round trip: every edge lands exactly once, no tenant
stream carries a foreign edge, and every target is restored exactly once.

Key classes:
- (none)

Key functions:
- global_stream_indices: the full-context edge index stream (sorted edge order).
- tenant_stream_indices: one agency's owned-edge index stream.
- validate_tenant_partition: assert the tenant streams exactly partition the edges.
- concatenate_stream_features: scatter per-stream features back to original edge order.

Notes:
- Ownership uses `GfpEdgeSet.source_agency` (already derived via `demo_agency_index`);
  this module never re-derives ownership from raw bank tokens.
- Graph state must never flow across scopes or tenants: callers create ONE engine per
  stream (Phase 4 adapters) — these helpers only carve index streams and re-assemble.
- `concatenate_stream_features` fails loudly on any double-fill or hole; a silent
  misalignment here would corrupt every downstream metric.
"""

from __future__ import annotations

import numpy as np

from lib.gfp.edges import GfpEdgeSet


def global_stream_indices(edge_set: GfpEdgeSet) -> np.ndarray:
    """Return the global-scope stream: every context edge in sorted edge order."""
    return np.arange(edge_set.gfp_matrix.shape[0], dtype=np.int64)


def tenant_stream_indices(edge_set: GfpEdgeSet, agency_index: int) -> np.ndarray:
    """Return one agency's owned-edge stream (source-node ownership), order preserved."""
    if agency_index < 0:
        raise ValueError("agency_index must be non-negative")
    return np.flatnonzero(edge_set.source_agency == agency_index).astype(np.int64)


def validate_tenant_partition(edge_set: GfpEdgeSet, agency_count: int) -> list[np.ndarray]:
    """Carve every tenant stream and assert they exactly partition the context edges.

    Returns the per-agency index streams. Raises when a stream carries a foreign edge,
    when any edge is lost or duplicated across streams, or when any TARGET edge would
    not be restored exactly once by concatenation.
    """
    if agency_count < 1:
        raise ValueError("agency_count must be >= 1")
    streams = [tenant_stream_indices(edge_set, agency) for agency in range(agency_count)]
    for agency, stream in enumerate(streams):
        if not np.all(edge_set.source_agency[stream] == agency):
            raise ValueError(f"tenant stream {agency} carries another agency's edge")
    stacked = np.concatenate(streams) if streams else np.empty(0, dtype=np.int64)
    n = edge_set.gfp_matrix.shape[0]
    if stacked.shape[0] != n or np.unique(stacked).shape[0] != n:
        raise ValueError("tenant streams must partition the context edges exactly once")
    target_indices = np.flatnonzero(edge_set.is_target)
    restored_targets = np.intersect1d(stacked, target_indices)
    if restored_targets.shape[0] != target_indices.shape[0]:
        raise ValueError("concatenating tenant streams must restore every target exactly once")
    return streams


def concatenate_stream_features(
    total_rows: int, streams: list[tuple[np.ndarray, np.ndarray]]
) -> np.ndarray:
    """Scatter per-stream feature blocks back into original edge order (exactly once).

    `streams` pairs each stream's edge indices with its (len(indices), k) feature block.
    The result is float32 (engineered columns only — plan Phase 4 drops identifiers).
    """
    if total_rows < 1:
        raise ValueError("total_rows must be positive")
    if not streams:
        raise ValueError("at least one stream is required")
    width = streams[0][1].shape[1]
    out = np.zeros((total_rows, width), dtype=np.float32)
    filled = np.zeros(total_rows, dtype=bool)
    for indices, features in streams:
        if indices.shape[0] != features.shape[0] or features.shape[1] != width:
            raise ValueError("stream features must align with stream indices and width")
        if np.any(filled[indices]):
            raise ValueError("a row was produced by more than one stream")
        out[indices] = features.astype(np.float32)
        filled[indices] = True
    if not filled.all():
        raise ValueError("concatenation left unfilled rows — a stream is missing edges")
    return out

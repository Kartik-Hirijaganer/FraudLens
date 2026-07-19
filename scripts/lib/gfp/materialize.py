"""Summary: Fold-safe batched GFP feature materialization for the offline benchmark
(GFP plan Phase 5; serving boundary: ADR-017). One engine instance is created per
scope/tenant stream and fed the stream's edges in configured-size batches that NEVER
cross a chronological fold boundary, so graph state flows train -> calibration ->
holdout and never backward. The global scope is the full context stream; the
per-tenant scope runs one FRESH engine per agency over only that agency's owned
edges and scatters the per-stream feature blocks back into original edge order via
the validated partition helpers. All GFP feature groups are materialized once per
scope; arms B and C are projected from that single matrix downstream.

Key classes:
- (none)

Key functions:
- fold_safe_batches: yield (start, end) batch bounds that never cross a fold boundary.
- materialize_stream_features: run one fresh engine over one ordered edge stream.
- materialize_scope_features: materialize the full engineered matrix for one scope.

Notes:
- Streams must already be ordered by (timestamp, originalRowId) — `GfpEdgeSet` builds
  them that way and per-tenant index streams preserve that order.
- Engine outputs are validated per batch (row alignment, width, finiteness) and
  narrowed to float32: only engineered columns ever leave this module.
- A fresh engine per stream is the no-state-leak rule: graph state never crosses
  scopes, tenants, or datasets (plan "Temporal / leakage" contract).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import numpy as np

from lib.gfp.edges import GfpEdgeSet
from lib.gfp.protocol import GraphPreprocessor
from lib.gfp.scopes import concatenate_stream_features, validate_tenant_partition

# A scope is either the full context graph or one agency-owned tenant graph.
GLOBAL_SCOPE = "global"
PER_TENANT_SCOPE = "per_tenant"

EngineFactory = Callable[[], GraphPreprocessor]


def fold_safe_batches(folds: np.ndarray, batch_size: int) -> Iterator[tuple[int, int]]:
    """Yield [start, end) batch bounds of at most `batch_size` rows within one fold.

    A batch never spans two folds: the plan's leakage contract feeds GFP in batches
    "that never cross a fold boundary", so a fold boundary always forces a new batch.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    fold_ids = np.asarray(folds)
    n = int(fold_ids.shape[0])
    start = 0
    while start < n:
        end = start + 1
        while end < n and end - start < batch_size and fold_ids[end] == fold_ids[start]:
            end += 1
        yield start, end
        start = end


def materialize_stream_features(
    engine: GraphPreprocessor, matrix: np.ndarray, folds: np.ndarray, batch_size: int
) -> np.ndarray:
    """Run one engine over one ordered stream in fold-safe batches; return float32 features.

    The engine must be FRESH for this stream (its accumulated graph state must not
    predate the stream) — enforcement is structural: callers pass a factory, never a
    shared instance.
    """
    if matrix.shape[0] != np.asarray(folds).shape[0]:
        raise ValueError("stream folds must align 1:1 with the stream matrix")
    width = len(engine.feature_names)
    out = np.zeros((matrix.shape[0], width), dtype=np.float32)
    for start, end in fold_safe_batches(folds, batch_size):
        block = np.asarray(engine.transform_batch(matrix[start:end]))
        if block.shape != (end - start, width):
            raise ValueError(
                f"engine emitted a misaligned feature block {block.shape} for a "
                f"{end - start}-row batch of {width} features"
            )
        if not np.isfinite(block).all():
            raise ValueError("engine emitted non-finite feature values")
        out[start:end] = block.astype(np.float32)
    return out


def materialize_scope_features(
    edge_set: GfpEdgeSet,
    engine_factory: EngineFactory,
    *,
    scope: str,
    batch_size: int,
    agency_count: int,
) -> np.ndarray:
    """Materialize the complete engineered feature matrix for one graph scope.

    `global` feeds every context edge to ONE fresh engine; `per_tenant` runs one fresh
    engine per agency over only that agency's owned edges (counterparty nodes still
    appear as endpoints; other agencies' EDGES never do) and concatenates the blocks
    back into original edge order, every row restored exactly once.
    """
    if scope == GLOBAL_SCOPE:
        return materialize_stream_features(
            engine_factory(), edge_set.gfp_matrix, edge_set.folds, batch_size
        )
    if scope != PER_TENANT_SCOPE:
        raise ValueError(f"unknown scope '{scope}' (expected global or per_tenant)")
    streams = validate_tenant_partition(edge_set, agency_count)
    blocks: list[tuple[np.ndarray, np.ndarray]] = []
    for indices in streams:
        features = materialize_stream_features(
            engine_factory(),
            edge_set.gfp_matrix[indices],
            edge_set.folds[indices],
            batch_size,
        )
        blocks.append((indices, features))
    return concatenate_stream_features(edge_set.gfp_matrix.shape[0], blocks)

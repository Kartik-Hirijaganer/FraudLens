"""Summary: The typed engine seam for the offline GFP benchmark (GFP plan Phase 4).
`GraphPreprocessor` is the structural protocol every engine satisfies — the pure
reference engine (the oracle), the deterministic fake (orchestration tests), and the
snapml adapter (published runs). Engines receive explicit float64 edge batches in the
canonical `[edge_id, dense_src, dense_dst, utc_epoch_s, usd_amount]` order, accumulate
graph state batch over batch (train -> calibration -> holdout, never backward), and
return ONLY engineered feature columns aligned 1:1 with the input rows. Engines never
mutate caller arrays. `validate_edge_batch` is the shared wire-schema gate.

Key classes:
- GraphPreprocessor: the structural engine protocol (feature_names + transform_batch).

Key functions:
- validate_edge_batch: assert one batch honors the canonical wire schema.

Notes:
- Batches must arrive ordered by (timestamp, originalRowId) and must never cross a fold
  boundary (the orchestrator owns both rules; engines only validate the wire schema).
- transform_batch return dtype is floating (reference emits float64; the snapml adapter
  narrows to float32 per the plan) with shape (len(batch), len(feature_names)).
- Labels, agencies, and fold ids are metadata on GfpEdgeSet — they are structurally
  absent from the 5-column engine matrix, so no engine can ever see them.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from lib.gfp.config import CANONICAL_EDGE_COLUMNS

_MAX_SAFE_FLOAT_INT = float(1 << 53)  # float64 integer-exactness bound (wire contract)
_BATCH_DIMENSIONS = 2  # the wire format is a 2-D (rows x columns) array


@runtime_checkable
class GraphPreprocessor(Protocol):
    """Structural protocol for GFP engines (reference, fake, snapml adapter)."""

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Engineered feature names, in the exact column order transform_batch emits."""
        ...  # pragma: no cover - protocol declaration

    def transform_batch(self, edge_batch: np.ndarray) -> np.ndarray:
        """Ingest one canonical edge batch; return engineered features per input row."""
        ...  # pragma: no cover - protocol declaration


def validate_edge_batch(edge_batch: np.ndarray) -> None:
    """Assert one batch honors the canonical wire schema (shape, dtype, magnitude, ids)."""
    if not isinstance(edge_batch, np.ndarray) or edge_batch.ndim != _BATCH_DIMENSIONS:
        raise ValueError("edge batch must be a 2-D numpy array")
    if edge_batch.shape[1] != len(CANONICAL_EDGE_COLUMNS):
        raise ValueError(
            f"edge batch must carry exactly {len(CANONICAL_EDGE_COLUMNS)} columns "
            f"({', '.join(CANONICAL_EDGE_COLUMNS)})"
        )
    if not np.issubdtype(edge_batch.dtype, np.floating):
        raise ValueError("edge batch must be floating (float64 wire format)")
    if edge_batch.shape[0] == 0:
        return
    if not np.isfinite(edge_batch).all():
        raise ValueError("edge batch carries non-finite values")
    if float(np.abs(edge_batch).max()) >= _MAX_SAFE_FLOAT_INT:
        raise ValueError("edge batch values must stay below 2^53")
    edge_ids = edge_batch[:, 0]
    if np.unique(edge_ids).shape[0] != edge_ids.shape[0]:
        raise ValueError("edge ids must be unique within a batch")

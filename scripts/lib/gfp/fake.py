"""Summary: Deterministic schema-correct FAKE GFP engine (GFP plan Phase 4). It satisfies
the `GraphPreprocessor` protocol — right width, right names, right row alignment, stable
across calls — while producing values from a trivial arithmetic pattern with NO graph
semantics at all. Orchestration tests (Phase 5) use it to exercise plumbing without
snapml; the known-answer graph tests must FAIL on it by construction, which is asserted
in the engine test suite so the fake can never quietly impersonate a real engine.

Key classes:
- FakeGraphPreprocessor: stateless protocol-satisfying transformer with fake values.

Key functions:
- (none)

Notes:
- Values depend only on (edge_id, column index) so runs are reproducible and alignment
  bugs (row shuffles) still surface in tests that use the fake.
- Deliberately stateless: feeding batches in any split yields identical rows, unlike the
  real engines whose graph state accumulates — another intentional known-answer breaker.
"""

from __future__ import annotations

import numpy as np

from lib.gfp.protocol import validate_edge_batch
from lib.gfp.schema import GraphFeatureConfig, GraphFeatureSchema

# Small co-prime multipliers keep fake values varied but obviously non-graph-derived.
_EDGE_MULTIPLIER = 37
_COLUMN_MULTIPLIER = 11
_MODULUS = 5


class FakeGraphPreprocessor:
    """Stateless, deterministic stand-in engine for orchestration tests (never results)."""

    def __init__(self, config: GraphFeatureConfig) -> None:
        """Derive the schema so the fake stays width/name-correct for any config."""
        self._schema = GraphFeatureSchema.from_config(config)

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Engineered names in emission order (identical to the real engines)."""
        return self._schema.feature_names

    def transform_batch(self, edge_batch: np.ndarray) -> np.ndarray:
        """Emit deterministic (edge_id, column)-derived values with zero graph semantics."""
        validate_edge_batch(edge_batch)
        n = edge_batch.shape[0]
        width = len(self._schema.feature_names)
        out = np.zeros((n, width), dtype=np.float64)
        if n == 0:
            return out
        edge_ids = np.asarray(edge_batch[:, 0], dtype=np.int64)
        columns = np.arange(width, dtype=np.int64)
        out[:] = (
            (edge_ids[:, None] * _EDGE_MULTIPLIER + columns[None, :] * _COLUMN_MULTIPLIER)
            % _MODULUS
        ).astype(np.float64)
        return out

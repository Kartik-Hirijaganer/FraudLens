"""Summary: The ONLY snapml integration point in FraudLens (GFP plan Phase 4; ADR-017).
`SnapMlGraphPreprocessor` wraps IBM Snap ML's `GraphFeaturePreprocessor` behind the same
`GraphPreprocessor` protocol as the reference engine: one instance per scope/tenant
stream, inputs defensively copied, output width and edge-id alignment validated on every
batch (realigned by edge id if the engine reorders), and ONLY the engineered columns
returned as float32 — identifiers never leave this module. The snapml import is lazy and
happens exactly once, inside the default engine factory, so every other module (and every
non-x86-64 machine) stays importable without the wheel.

Key classes:
- SnapMlGraphPreprocessor: protocol adapter around one snapml GraphFeaturePreprocessor.

Key functions:
- snapml_params: project a GraphFeatureConfig onto snapml's parameter dictionary.

Notes:
- Parameter keys and the output layout were pinned empirically against snapml 1.17.2
  (fan/degree/scatter-gather/temp-cycle/lc-cycle histograms then 4x13 vertex stats);
  `snapml_params` validates the config's endpoint/stat ordering matches that layout so
  generated names can never silently misalign.
- Memory budget: snapml keeps the transaction graph in memory. HI-Small (~5M edges) fits
  comfortably under ~8 GiB with the pandas frame; the node-induced Medium contexts are
  smaller. Run scopes SEQUENTIALLY and drop each adapter (and its arrays) before the
  next — the Phase-5 orchestrator owns that discipline.
- Published reports must say engine=snapml; reference/fake runs can never be promoted.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from lib.gfp.config import CANONICAL_EDGE_COLUMNS
from lib.gfp.protocol import validate_edge_batch
from lib.gfp.schema import GraphFeatureConfig, GraphFeatureSchema

# snapml vertex_stats_feats codes (library enum, probed):
# 0:fan 1:deg 2:ratio 3:avg 4:sum 5:min 6:max 7:median 8:var 9:skew 10:kurtosis
_VERTEX_STAT_CODES: dict[str, int] = {
    "fan": 0,
    "degree": 1,
    "ratio": 2,
    "avg": 3,
    "sum": 4,
    "var": 8,
    "skew": 9,
    "kurtosis": 10,
}
# The endpoint block order snapml emits (probed); configs must pin exactly this order.
_SNAPML_ENDPOINT_ORDER: tuple[str, ...] = ("source_out", "source_in", "target_out", "target_in")
_DEFAULT_NUM_THREADS = 4

EngineFactory = Callable[[], Any]


def _validate_layout_alignment(config: GraphFeatureConfig) -> None:
    """Reject configs whose ordering cannot align with snapml's fixed output layout."""
    if config.vertex_stats.endpoints != _SNAPML_ENDPOINT_ORDER:
        raise ValueError(
            f"vertex-stats endpoints must be {list(_SNAPML_ENDPOINT_ORDER)} in order to "
            "match snapml's emission layout"
        )
    codes = [_VERTEX_STAT_CODES[stat] for stat in config.vertex_stats.stats]
    if codes != sorted(codes):
        raise ValueError("vertex-stats stats must be listed in snapml code order")


def snapml_params(config: GraphFeatureConfig, *, num_threads: int) -> dict[str, Any]:
    """Project the engine-facing config onto snapml's parameter dictionary (pinned keys)."""
    _validate_layout_alignment(config)
    column_index = {name: position for position, name in enumerate(CANONICAL_EDGE_COLUMNS)}
    windows = config.windows
    return {
        "num_threads": num_threads,
        "time_window": windows.global_window_s,
        "vertex_stats": True,
        "vertex_stats_tw": windows.vertex_stats_window_s,
        "vertex_stats_cols": [column_index[name] for name in config.vertex_stats.raw_columns],
        "vertex_stats_feats": [_VERTEX_STAT_CODES[stat] for stat in config.vertex_stats.stats],
        "fan": True,
        "fan_tw": windows.fan_window_s,
        "fan_bins": list(range(config.fan_bins.lo, config.fan_bins.hi + 1)),
        "degree": True,
        "degree_tw": windows.degree_window_s,
        "degree_bins": list(range(config.degree_bins.lo, config.degree_bins.hi + 1)),
        "scatter-gather": True,
        "scatter-gather_tw": windows.scatter_gather_window_s,
        "scatter-gather_bins": list(
            range(config.scatter_gather_bins.lo, config.scatter_gather_bins.hi + 1)
        ),
        "temp-cycle": True,
        "temp-cycle_tw": windows.temporal_cycle_window_s,
        "temp-cycle_bins": list(
            range(config.temporal_cycle_bins.lo, config.temporal_cycle_bins.hi + 1)
        ),
        "lc-cycle": True,
        "lc-cycle_tw": windows.simple_cycle_window_s,
        "lc-cycle_len": config.simple_cycle_max_length,
        "lc-cycle_bins": list(range(config.simple_cycle_bins.lo, config.simple_cycle_bins.hi + 1)),
    }


def _import_snapml_engine() -> Any:
    """The single lazy snapml import in the codebase (ADR-017 dependency boundary)."""
    from snapml import GraphFeaturePreprocessor  # noqa: PLC0415 - THE lazy import (ADR-017)

    return GraphFeaturePreprocessor()


class SnapMlGraphPreprocessor:
    """Protocol adapter around ONE snapml engine (one instance per scope/tenant stream)."""

    def __init__(
        self,
        config: GraphFeatureConfig,
        *,
        num_threads: int = _DEFAULT_NUM_THREADS,
        engine_factory: EngineFactory | None = None,
    ) -> None:
        """Build and parameterize one engine; `engine_factory` is injectable for tests."""
        self._schema = GraphFeatureSchema.from_config(config)
        self._engine = (engine_factory or _import_snapml_engine)()
        self._engine.set_params(snapml_params(config, num_threads=num_threads))
        self._input_width = len(CANONICAL_EDGE_COLUMNS)

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Engineered names in the pinned snapml emission order."""
        return self._schema.feature_names

    def transform_batch(self, edge_batch: np.ndarray) -> np.ndarray:
        """Transform one batch: copy in, validate width, realign by edge id, drop ids."""
        validate_edge_batch(edge_batch)
        copied = np.array(edge_batch, dtype=np.float64, copy=True)
        out = np.asarray(self._engine.transform(copied), dtype=np.float64)
        expected = (edge_batch.shape[0], self._input_width + len(self._schema.feature_names))
        if out.shape != tuple(expected):
            raise ValueError(
                f"engine output shape {out.shape} != expected {expected} — the parameter "
                "projection and the generated schema have diverged"
            )
        if not np.array_equal(out[:, 0], edge_batch[:, 0]):
            position = {edge_id: row for row, edge_id in enumerate(out[:, 0].tolist())}
            try:
                permutation = [position[edge_id] for edge_id in edge_batch[:, 0].tolist()]
            except KeyError as exc:
                raise ValueError("engine output lost an input edge id") from exc
            out = out[np.asarray(permutation, dtype=np.int64)]
        return out[:, self._input_width :].astype(np.float32)

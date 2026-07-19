"""Summary: Canonical GFP feature naming + ordering, generated FROM the validated config
(GFP plan Phase 3; "no hand-maintained parallel lists"). `GraphFeatureConfig` is the
engine-facing projection of `GfpBenchmarkConfig`; `GraphFeatureSchema` derives the exact
engineered-column names in the empirically pinned snapml 1.17.2 output order: fan-in,
fan-out, degree-in, degree-out, scatter-gather, temporal-cycle, simple-cycle histograms,
then per-endpoint vertex statistics (structural fan/degree/ratio, then per-raw-column
moments). Module constants expose the Arm-B / Arm-C-increment / combined name tuples and
assert at import that they are disjoint from the 19 served `FEATURE_NAMES`.

Key classes:
- GraphFeatureConfig: the engine-facing GFP parameter projection (windows, bins, stats).
- GraphFeatureSchema: ordered engineered-feature names + family slices + arm groupings.

Key functions:
- histogram_cell_names: bin-range -> ordered `gfp_<pattern>_ge_<lo>[_lt_<hi>]` cell names.

Notes:
- Histogram cells: [lo, lo+1), ..., [hi-1, hi), then an unbounded `ge_<hi>` cell — the
  layout snapml produces for consecutive-integer bins (probed: k > hi accumulates in the
  last cell). Cell count per family = hi - lo + 1.
- Arm B = fan/degree histograms + vertex statistics; Arm C increment = scatter-gather +
  temporal-cycle + simple-cycle histograms (plan "Arms & canonical feature names").
- The vertex-stat block order per endpoint is structural stats first (fan, degree, ratio)
  then, per raw column in config order, the moment stats (avg, sum, var, skew, kurtosis).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fraudlens_ml.scoring.features import FEATURE_NAMES
from lib.gfp.config import (
    CANONICAL_EDGE_COLUMNS,
    GfpBenchmarkConfig,
    GfpBinRange,
    GfpVertexStatsConfig,
    GfpWindowsConfig,
    load_gfp_benchmark_config,
)

_NAME_PREFIX = "gfp"
# Vertex statistics that describe graph structure once per endpoint (no raw column).
_STRUCTURAL_STATS: tuple[str, ...] = ("fan", "degree", "ratio")
# Histogram families in the empirically pinned snapml output order.
_HISTOGRAM_FAMILIES: tuple[str, ...] = (
    "fan_in",
    "fan_out",
    "degree_in",
    "degree_out",
    "scatter_gather",
    "temporal_cycle",
    "simple_cycle",
)


class GraphFeatureConfig(BaseModel):
    """The engine-facing projection of the frozen benchmark protocol's GFP parameters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    windows: GfpWindowsConfig = Field(..., description="Per-family time windows, seconds.")
    fan_bins: GfpBinRange = Field(..., description="Fan in/out histogram bin range.")
    degree_bins: GfpBinRange = Field(..., description="Degree in/out histogram bin range.")
    scatter_gather_bins: GfpBinRange = Field(..., description="Scatter-gather bin range.")
    temporal_cycle_bins: GfpBinRange = Field(..., description="Temporal-cycle bin range.")
    simple_cycle_bins: GfpBinRange = Field(..., description="Simple-cycle bin range.")
    simple_cycle_max_length: int = Field(..., gt=1, description="Max simple-cycle length.")
    vertex_stats: GfpVertexStatsConfig = Field(
        ..., description="Vertex-statistic endpoints, statistics, and raw columns."
    )

    @classmethod
    def from_benchmark(cls, benchmark: GfpBenchmarkConfig) -> GraphFeatureConfig:
        """Project the full benchmark protocol onto the engine-facing parameter set."""
        return cls(
            windows=benchmark.windows,
            fan_bins=benchmark.bins.fan,
            degree_bins=benchmark.bins.degree,
            scatter_gather_bins=benchmark.bins.scatter_gather,
            temporal_cycle_bins=benchmark.bins.temporal_cycle,
            simple_cycle_bins=benchmark.bins.simple_cycle,
            simple_cycle_max_length=benchmark.simple_cycle_max_length,
            vertex_stats=benchmark.vertex_stats,
        )


def histogram_cell_names(pattern: str, bins: GfpBinRange) -> tuple[str, ...]:
    """Return the ordered cell names for one pattern family's histogram.

    Cells cover [lo, lo+1) ... [hi-1, hi) plus the unbounded `ge_<hi>` overflow cell that
    snapml accumulates every k >= hi into (probed behavior).
    """
    bounded = [f"{_NAME_PREFIX}_{pattern}_ge_{k}_lt_{k + 1}" for k in range(bins.lo, bins.hi)]
    return (*bounded, f"{_NAME_PREFIX}_{pattern}_ge_{bins.hi}")


def _vertex_stat_names(vertex_stats: GfpVertexStatsConfig) -> tuple[str, ...]:
    """Return the ordered vertex-statistic names across endpoints (snapml block order)."""
    structural = [stat for stat in vertex_stats.stats if stat in _STRUCTURAL_STATS]
    moments = [stat for stat in vertex_stats.stats if stat not in _STRUCTURAL_STATS]
    names: list[str] = []
    for endpoint in vertex_stats.endpoints:
        names.extend(f"{_NAME_PREFIX}_{endpoint}_{stat}" for stat in structural)
        for column in vertex_stats.raw_columns:
            names.extend(f"{_NAME_PREFIX}_{endpoint}_{column}_{stat}" for stat in moments)
    return tuple(names)


class GraphFeatureSchema(BaseModel):
    """Ordered engineered-feature names, family slices, and arm groupings for one config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    feature_names: tuple[str, ...] = Field(
        ..., min_length=1, description="All engineered names in engine output order."
    )
    family_names: dict[str, tuple[str, ...]] = Field(
        ..., description="Ordered names per family (histograms + 'vertex_stats')."
    )
    arm_b_names: tuple[str, ...] = Field(
        ..., description="Arm B increment: fan/degree histograms + vertex statistics."
    )
    arm_c_increment_names: tuple[str, ...] = Field(
        ..., description="Arm C increment: scatter-gather + temporal + simple cycles."
    )

    @model_validator(mode="after")
    def _partition_is_exact(self) -> GraphFeatureSchema:
        """Arms B and C partition the full name set; no duplicates anywhere."""
        if len(set(self.feature_names)) != len(self.feature_names):
            raise ValueError("engineered feature names must be unique")
        combined = set(self.arm_b_names) | set(self.arm_c_increment_names)
        if combined != set(self.feature_names) or (
            set(self.arm_b_names) & set(self.arm_c_increment_names)
        ):
            raise ValueError("arm B + arm C increment must exactly partition the features")
        return self

    @classmethod
    def from_config(cls, config: GraphFeatureConfig) -> GraphFeatureSchema:
        """Derive the schema for one validated engine config (never hand-maintained)."""
        families: dict[str, tuple[str, ...]] = {
            "fan_in": histogram_cell_names("fan_in", config.fan_bins),
            "fan_out": histogram_cell_names("fan_out", config.fan_bins),
            "degree_in": histogram_cell_names("degree_in", config.degree_bins),
            "degree_out": histogram_cell_names("degree_out", config.degree_bins),
            "scatter_gather": histogram_cell_names("scatter_gather", config.scatter_gather_bins),
            "temporal_cycle": histogram_cell_names("temporal_cycle", config.temporal_cycle_bins),
            "simple_cycle": histogram_cell_names("simple_cycle", config.simple_cycle_bins),
            "vertex_stats": _vertex_stat_names(config.vertex_stats),
        }
        ordered: tuple[str, ...] = tuple(
            name for family in (*_HISTOGRAM_FAMILIES, "vertex_stats") for name in families[family]
        )
        arm_b = (
            *families["fan_in"],
            *families["fan_out"],
            *families["degree_in"],
            *families["degree_out"],
            *families["vertex_stats"],
        )
        arm_c = (
            *families["scatter_gather"],
            *families["temporal_cycle"],
            *families["simple_cycle"],
        )
        return cls(
            feature_names=ordered,
            family_names=families,
            arm_b_names=arm_b,
            arm_c_increment_names=arm_c,
        )

    def column_indices(self, names: tuple[str, ...]) -> tuple[int, ...]:
        """Map a name subset onto engineered-column indices (projection helper)."""
        position = {name: index for index, name in enumerate(self.feature_names)}
        return tuple(position[name] for name in names)


VertexEndpoint = Literal["source_out", "source_in", "target_out", "target_in"]

# Canonical schema derived from the COMMITTED protocol pins; the module-level names below
# are what serving-guard tests compare against the 19 served features.
_CANONICAL_SCHEMA = GraphFeatureSchema.from_config(
    GraphFeatureConfig.from_benchmark(load_gfp_benchmark_config())
)
GRAPH_ARM_B_FEATURE_NAMES: tuple[str, ...] = _CANONICAL_SCHEMA.arm_b_names
GRAPH_ARM_C_INCREMENT_FEATURE_NAMES: tuple[str, ...] = _CANONICAL_SCHEMA.arm_c_increment_names
GRAPH_FEATURE_NAMES: tuple[str, ...] = _CANONICAL_SCHEMA.feature_names

if not set(GRAPH_FEATURE_NAMES).isdisjoint(FEATURE_NAMES):  # pragma: no cover - import guard
    raise AssertionError("GFP feature names may never collide with the served FEATURE_NAMES")
if any(name in CANONICAL_EDGE_COLUMNS for name in GRAPH_FEATURE_NAMES):  # pragma: no cover
    raise AssertionError("engineered names may never shadow the edge wire schema")

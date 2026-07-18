"""Summary: Frozen, typed protocol pins for the offline GFP tenant-isolation benchmark
(GFP study plan Phase 2; serving boundary: ADR-017). `GfpBenchmarkConfig` loads
`config/gfp-benchmark.yaml` and rejects bad windows, bin ranges, hash/fold fractions,
target quotas, paths, USD rates, and engine versions BEFORE any benchmark code runs, so a
mis-pinned protocol can never produce a publishable result. The protocol was frozen with
ADR-017 before any new holdout result was inspected — changing pins invalidates results.

Key classes:
- GfpEngineConfig: pinned engine identity (snapml + exact version at or above the 1.15 floor).
- GfpWindowsConfig: per-pattern-family time windows in seconds (all strictly positive).
- GfpBinRange: one inclusive histogram bin range (lo >= 2, hi > lo).
- GfpBinsConfig: bin ranges for the five histogram pattern families.
- GfpVertexStatsConfig: vertex-statistic endpoints, statistics, and raw numeric columns.
- GfpDatasetConfig: one dataset's fetch-registry source, graph context, and target quota.
- GfpSamplingConfig: the label-blind node-hash fraction escalation ladder.
- GfpFoldFractionsConfig: chronological train/calibration/holdout fold fractions.
- GfpTargetQuotasConfig: stratified per-fold target quotas for node-induced datasets.
- GfpPathsConfig: benchmark IO directories, forced under gitignored .local/.
- GfpBenchmarkConfig: the full frozen benchmark protocol (root model, cross-validated).

Key functions:
- load_gfp_benchmark_config: parse + validate config/gfp-benchmark.yaml into the model.

Notes:
- Fractions are exact rationals ("1/4", "3/5") so ladder ordering and fold sums never
  drift on binary floats; they are validated with `fractions.Fraction`.
- Paths must stay relative and under .local/ (gitignored): benchmark inputs/outputs never
  reach committed repo paths, and the model rejects absolute or upward-traversing paths.
- usd_rates are the study's FIXED currency pins (synthetic 2022-era magnitudes): edge
  amounts must be single-unit for graph statistics, and an unknown currency is an error —
  never silently treated as USD (plan Phase 3).
- The exact engine pin must match the root `gfp` dependency group (snapml==1.17.2);
  published reports must say engine=snapml (reference-engine runs are never promoted).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from fractions import Fraction
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_GFP_BENCHMARK_CONFIG = _REPO_ROOT / "config" / "gfp-benchmark.yaml"

# The GFP edge-list wire schema; the benchmark contract pins the exact names AND order.
CANONICAL_EDGE_COLUMNS: tuple[str, ...] = (
    "edge_id",
    "dense_src",
    "dense_dst",
    "utc_epoch_s",
    "usd_amount",
)

# snapml floor confirmed against published cp311 x86-64 wheels; exact pin lives in the
# YAML + the root `gfp` dependency group.
_ENGINE_VERSION_FLOOR = (1, 15)
_MIN_BIN_LOWER = 2  # a graph pattern needs at least two participants; smaller bins are noise
_MIN_CYCLE_LENGTH = 2
_LOCAL_SCRATCH_ROOT = ".local"  # gitignored; the only area benchmark IO may touch
_MIN_LOCAL_PATH_PARTS = 2  # a named subdirectory under .local/, never the bare scratch root
_UNIT = Fraction(1)

_VertexEndpoint = Literal["source_out", "source_in", "target_out", "target_in"]
_VertexStat = Literal["fan", "degree", "ratio", "avg", "sum", "var", "skew", "kurtosis"]


def _parse_fraction(value: str, *, field: str) -> Fraction:
    """Parse an exact 'num/den' (or integer) rational string, rejecting anything malformed."""
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{field}: '{value}' is not a valid rational number") from exc


class GfpEngineConfig(BaseModel):
    """Pinned identity of the graph-feature engine used for published results."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Literal["snapml"] = Field(
        ..., description="Engine name; published results are snapml-only (ADR-017)."
    )
    version: str = Field(..., description="Exact engine version pin, e.g. '1.17.2' (floor: 1.15).")

    @field_validator("version")
    @classmethod
    def _version_meets_floor(cls, value: str) -> str:
        """Reject malformed versions and anything below the wheel-confirmed 1.15 floor."""
        parts = value.split(".")
        if not parts or not all(part.isdigit() for part in parts):
            raise ValueError(f"engine version must be dotted integers, got '{value}'")
        if tuple(int(part) for part in parts[:2]) < _ENGINE_VERSION_FLOOR:
            floor = ".".join(str(part) for part in _ENGINE_VERSION_FLOOR)
            raise ValueError(f"engine version {value} is below the supported floor {floor}")
        return value


class GfpWindowsConfig(BaseModel):
    """Per-pattern-family GFP time windows in seconds (contract pins 86,400 / 21,600)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    global_window_s: int = Field(..., gt=0, description="Base graph time window, seconds.")
    fan_window_s: int = Field(..., gt=0, description="Fan-in/fan-out histogram window, seconds.")
    degree_window_s: int = Field(..., gt=0, description="Degree histogram window, seconds.")
    vertex_stats_window_s: int = Field(..., gt=0, description="Vertex-statistics window, seconds.")
    temporal_cycle_window_s: int = Field(..., gt=0, description="Temporal-cycle window, seconds.")
    simple_cycle_window_s: int = Field(
        ..., gt=0, description="Length-constrained simple-cycle window, seconds."
    )
    scatter_gather_window_s: int = Field(..., gt=0, description="Scatter-gather window, seconds.")


class GfpBinRange(BaseModel):
    """One inclusive histogram bin range [lo, hi] for a pattern family."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lo: int = Field(..., ge=_MIN_BIN_LOWER, description="Lowest bin boundary (>= 2).")
    hi: int = Field(..., description="Highest bin boundary; must exceed lo.")

    @model_validator(mode="after")
    def _ordered(self) -> GfpBinRange:
        """Reject empty or inverted bin ranges."""
        if self.hi <= self.lo:
            raise ValueError(f"bin range must satisfy hi > lo, got [{self.lo}, {self.hi}]")
        return self


class GfpBinsConfig(BaseModel):
    """Histogram bin ranges per pattern family (contract: 2..30; simple-cycle 2..10)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fan: GfpBinRange = Field(..., description="Fan pattern histogram bins.")
    degree: GfpBinRange = Field(..., description="Degree histogram bins.")
    scatter_gather: GfpBinRange = Field(..., description="Scatter-gather histogram bins.")
    temporal_cycle: GfpBinRange = Field(..., description="Temporal-cycle histogram bins.")
    simple_cycle: GfpBinRange = Field(
        ..., description="Length-constrained simple-cycle histogram bins."
    )


class GfpVertexStatsConfig(BaseModel):
    """Vertex-statistic configuration: endpoints x statistics over raw numeric edge columns."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoints: tuple[_VertexEndpoint, ...] = Field(
        ..., min_length=1, description="Aggregation endpoints (source/target x in/out)."
    )
    stats: tuple[_VertexStat, ...] = Field(
        ..., min_length=1, description="Statistics computed per endpoint."
    )
    raw_columns: tuple[str, ...] = Field(
        ...,
        min_length=1,
        description="Numeric edge columns the statistics aggregate (timestamp + USD amount).",
    )

    @model_validator(mode="after")
    def _unique_and_known_columns(self) -> GfpVertexStatsConfig:
        """Reject duplicate entries and raw columns outside the canonical edge schema."""
        members: tuple[tuple[str, tuple[str, ...]], ...] = (
            ("endpoints", self.endpoints),
            ("stats", self.stats),
            ("raw_columns", self.raw_columns),
        )
        for name, values in members:
            if len(set(values)) != len(values):
                raise ValueError(f"vertex-stats {name} contains duplicates: {list(values)}")
        unknown = [col for col in self.raw_columns if col not in CANONICAL_EDGE_COLUMNS]
        if unknown:
            raise ValueError(f"vertex-stats raw_columns not in the edge schema: {unknown}")
        return self


class GfpDatasetConfig(BaseModel):
    """One benchmark dataset: fetch-registry source id, graph context, and target quota."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(
        ..., min_length=1, description="Fetch-registry source id (scripts/fetch_dataset.py)."
    )
    graph_context: Literal["full", "node_induced"] = Field(
        ...,
        description="Whether GFP sees every servable row or a label-blind node-induced subgraph.",
    )
    target_quota: int | None = Field(
        default=None,
        gt=0,
        description="Stratified target cap for node-induced datasets; None = all servable rows.",
    )

    @model_validator(mode="after")
    def _quota_matches_context(self) -> GfpDatasetConfig:
        """node_induced requires a target quota; full-context datasets must not set one."""
        if self.graph_context == "node_induced" and self.target_quota is None:
            raise ValueError(f"dataset '{self.source}': node_induced requires target_quota")
        if self.graph_context == "full" and self.target_quota is not None:
            raise ValueError(f"dataset '{self.source}': full context must not set target_quota")
        return self


class GfpSamplingConfig(BaseModel):
    """Label-blind node-induced sampling: the SHA-256 hash-fraction escalation ladder."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_hash_fractions: tuple[str, ...] = Field(
        ...,
        min_length=1,
        description=(
            "Escalation ladder of node-keep hash fractions (exact rationals, e.g. '1/4'); "
            "escalate one step only when a fold lacks a class, then fail — never silently resize."
        ),
    )

    @field_validator("node_hash_fractions")
    @classmethod
    def _ladder_valid(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Each entry is an exact rational in (0, 1]; the ladder strictly increases."""
        parsed = [_parse_fraction(value, field="node_hash_fractions") for value in values]
        for fraction in parsed:
            if not 0 < fraction <= _UNIT:
                raise ValueError(f"hash fraction {fraction} is outside (0, 1]")
        if any(nxt <= prev for prev, nxt in pairwise(parsed)):
            raise ValueError(f"hash-fraction ladder must strictly increase: {list(values)}")
        return values


class GfpFoldFractionsConfig(BaseModel):
    """Strict chronological fold fractions; exact rationals that must sum to exactly 1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    train: str = Field(..., description="Train fold fraction (exact rational, e.g. '3/5').")
    calibration: str = Field(..., description="Calibration fold fraction (exact rational).")
    holdout: str = Field(..., description="Holdout fold fraction (exact rational).")

    @model_validator(mode="after")
    def _sums_to_one(self) -> GfpFoldFractionsConfig:
        """Each fraction lies in (0, 1); the three sum to exactly 1 (no float drift)."""
        parts = {
            name: _parse_fraction(getattr(self, name), field=f"folds.{name}")
            for name in ("train", "calibration", "holdout")
        }
        for name, fraction in parts.items():
            if not 0 < fraction < _UNIT:
                raise ValueError(f"folds.{name}: {fraction} is outside (0, 1)")
        total = sum(parts.values())
        if total != _UNIT:
            raise ValueError(f"fold fractions must sum to exactly 1, got {total}")
        return self


class GfpTargetQuotasConfig(BaseModel):
    """Stratified per-fold target quotas applied to node-induced datasets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    train: int = Field(..., gt=0, description="Training-fold target quota.")
    calibration: int = Field(..., gt=0, description="Calibration-fold target quota.")
    holdout: int = Field(..., gt=0, description="Holdout-fold target quota.")

    @property
    def total(self) -> int:
        """The summed per-fold quota; must equal each sampled dataset's target_quota."""
        return self.train + self.calibration + self.holdout


class GfpPathsConfig(BaseModel):
    """Benchmark IO locations; both must stay relative and inside gitignored .local/."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    data_dir: str = Field(
        ..., description="Directory of fetched dataset CSVs (read-only to the benchmark)."
    )
    output_dir: str = Field(
        ..., description="Per-run study output directory (never committed, never served)."
    )

    @field_validator("data_dir", "output_dir")
    @classmethod
    def _relative_under_local(cls, value: str) -> str:
        """Reject absolute paths, traversal, and anything outside the .local/ scratch area."""
        path = PurePosixPath(value)
        if path.is_absolute() or value.startswith("~"):
            raise ValueError(f"path must be repo-relative, got '{value}'")
        if ".." in path.parts:
            raise ValueError(f"path must not traverse upward, got '{value}'")
        if path.parts[:1] != (_LOCAL_SCRATCH_ROOT,) or len(path.parts) < _MIN_LOCAL_PATH_PARTS:
            raise ValueError(f"path must live under {_LOCAL_SCRATCH_ROOT}/, got '{value}'")
        return value


class GfpBenchmarkConfig(BaseModel):
    """The full frozen GFP benchmark protocol, loaded from config/gfp-benchmark.yaml."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seed: int = Field(..., ge=0, description="Global deterministic seed (contract pins 1729).")
    batch_size: int = Field(
        ...,
        gt=0,
        description="GFP edge batch size; batches never cross folds (contract pins 128).",
    )
    engine: GfpEngineConfig = Field(
        ..., description="Pinned engine identity required for published results."
    )
    edge_columns: tuple[str, ...] = Field(
        ..., description="GFP edge-list schema; must equal the canonical columns in order."
    )
    windows: GfpWindowsConfig = Field(..., description="Per-family time windows, seconds.")
    bins: GfpBinsConfig = Field(..., description="Histogram bin ranges per pattern family.")
    simple_cycle_max_length: int = Field(
        ...,
        ge=_MIN_CYCLE_LENGTH,
        description="Maximum simple-cycle length searched (contract pins 10).",
    )
    vertex_stats: GfpVertexStatsConfig = Field(
        ..., description="Vertex-statistic endpoints/statistics/columns."
    )
    datasets: tuple[GfpDatasetConfig, ...] = Field(
        ..., min_length=1, description="The benchmark datasets (fetch-registry sources)."
    )
    sampling: GfpSamplingConfig = Field(
        ..., description="Label-blind node-induced sampling ladder."
    )
    folds: GfpFoldFractionsConfig = Field(
        ..., description="Chronological train/calibration/holdout fold fractions."
    )
    target_quotas: GfpTargetQuotasConfig = Field(
        ..., description="Stratified per-fold target quotas for node-induced datasets."
    )
    paths: GfpPathsConfig = Field(..., description="Benchmark IO directories under .local/.")
    usd_rates: dict[str, str] = Field(
        ...,
        min_length=1,
        description=(
            "Fixed USD-per-unit conversion pins keyed by the dataset's lower-cased currency "
            "names; a currency absent here is REJECTED at edge build (never assumed USD)."
        ),
    )

    @field_validator("usd_rates")
    @classmethod
    def _rates_positive(cls, value: dict[str, str]) -> dict[str, str]:
        """Keys are normalized lower-case names; every rate is a positive decimal string."""
        normalized: dict[str, str] = {}
        for name, rate in value.items():
            key = name.strip().lower()
            if not key or key in normalized:
                raise ValueError(f"usd_rates has a blank or duplicate currency name: '{name}'")
            try:
                parsed = Decimal(rate)
            except InvalidOperation as exc:
                raise ValueError(f"usd_rates['{name}'] = '{rate}' is not a decimal") from exc
            if parsed <= 0:
                raise ValueError(f"usd_rates['{name}'] must be positive, got {rate}")
            normalized[key] = rate
        return normalized

    @field_validator("edge_columns")
    @classmethod
    def _canonical_edges(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """The edge schema is part of the frozen contract; reject any rename or reorder."""
        if value != CANONICAL_EDGE_COLUMNS:
            raise ValueError(
                f"edge_columns must be exactly {list(CANONICAL_EDGE_COLUMNS)} in order, "
                f"got {list(value)}"
            )
        return value

    @model_validator(mode="after")
    def _cross_checks(self) -> GfpBenchmarkConfig:
        """Sources unique; sampled quotas equal the fold-quota total; cycle bins fit the cap."""
        sources = [dataset.source for dataset in self.datasets]
        if len(set(sources)) != len(sources):
            raise ValueError(f"duplicate dataset sources: {sources}")
        quota_total = self.target_quotas.total
        for dataset in self.datasets:
            if dataset.target_quota is not None and dataset.target_quota != quota_total:
                raise ValueError(
                    f"dataset '{dataset.source}' target_quota {dataset.target_quota} != "
                    f"per-fold quota total {self.target_quotas.total}"
                )
        if self.bins.simple_cycle.hi > self.simple_cycle_max_length:
            raise ValueError(
                f"simple-cycle bins reach {self.bins.simple_cycle.hi} but the searched cycle "
                f"length is capped at {self.simple_cycle_max_length}"
            )
        return self


def load_gfp_benchmark_config(path: Path | None = None) -> GfpBenchmarkConfig:
    """Load + validate the frozen benchmark protocol (defaults to the committed pin file)."""
    target = path or DEFAULT_GFP_BENCHMARK_CONFIG
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{target} must contain a YAML mapping, got {type(raw).__name__}")
    return GfpBenchmarkConfig.model_validate(raw)

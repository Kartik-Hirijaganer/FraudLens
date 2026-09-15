"""Summary: Exact-rational, whole-timestamp-cohort temporal fold materialization.

Key classes:
- FoldSlice: rows, positives, and prevalence for one temporal slice.
- FoldCounts: train, tuning, calibration, and holdout composition.
- FoldManifest: cohort-safe boundary and prevalence provenance.

Key functions:
- build_folds: split feature Parquet chronologically and persist the manifest.
- folded_path: resolve one fold-tagged Parquet artifact.
- fold_manifest_path: resolve one fold manifest sidecar.
- load_fold_manifest: load one validated fold manifest.

Notes:
- The middle fifth is split chronologically into tuning and calibration halves.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import duckdb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lib.fulldata.config import FullDataConfig, FullDataDataset
from lib.fulldata.features import feature_path
from lib.study import atomic_write_model

_FOLD_NAMES = ("train", "tuning", "calibration", "holdout")


def _sql_text(value: str) -> str:
    """Quote a configuration-controlled SQL string literal."""
    return "'" + value.replace("'", "''") + "'"


class FoldSlice(BaseModel):
    """Count and positive prevalence for one temporal slice."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: int = Field(..., ge=0, description="Rows assigned to this slice.")
    positives: int = Field(..., ge=0, description="Positive labels in this slice.")
    prevalence: float = Field(..., ge=0.0, le=1.0, description="Positive-label share.")


class FoldCounts(BaseModel):
    """Composition of every training/evaluation temporal slice."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    train: FoldSlice = Field(..., description="Earliest three-fifths training slice.")
    tuning: FoldSlice = Field(..., description="Early half of the middle fifth.")
    calibration: FoldSlice = Field(..., description="Late half of the middle fifth.")
    holdout: FoldSlice = Field(..., description="Latest fifth final-test slice.")


class FoldManifest(BaseModel):
    """Cohort-safe temporal boundaries and fold composition for one source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: str = Field(..., description="Configured candidate identity.")
    source: str = Field(..., description="Fetch-registry source identity.")
    fractions: tuple[str, str, str] = Field(..., description="Outer exact-rational fractions.")
    calibration_fractions: tuple[str, str] = Field(..., description="Middle-fold fractions.")
    boundary_epochs: tuple[int, int, int] = Field(..., description="Train/tuning/calibration ends.")
    counts: FoldCounts = Field(..., description="Rows, positives, and prevalence by slice.")

    @model_validator(mode="after")
    def _ordered(self) -> FoldManifest:
        if tuple(sorted(self.boundary_epochs)) != self.boundary_epochs:
            raise ValueError("fold boundaries must be chronological")
        return self


def folded_path(config: FullDataConfig, dataset: FullDataDataset, repo_root: Path) -> Path:
    """Resolve one source's fold-tagged Parquet file."""
    return repo_root / config.paths.work_dir / "folds" / dataset.source / "part-00000.parquet"


def fold_manifest_path(config: FullDataConfig, dataset: FullDataDataset, repo_root: Path) -> Path:
    """Resolve one source's temporal-fold manifest."""
    return repo_root / config.paths.work_dir / "folds" / dataset.source / "manifest.json"


def _cohort_boundary(connection: duckdb.DuckDBPyConnection, source: Path, target: int) -> int:
    """Return the timestamp cohort containing the zero-based nominal boundary row."""
    row = connection.execute(
        "SELECT cohort_key FROM read_parquet(?) ORDER BY cohort_key, row_id LIMIT 1 OFFSET ?",
        [str(source), max(0, target - 1)],
    ).fetchone()
    if row is None:
        raise ValueError("cannot build folds from an empty feature matrix")
    return int(row[0])


def _slice(connection: duckdb.DuckDBPyConnection, path: Path, name: str) -> FoldSlice:
    row = connection.execute(
        "SELECT count(*), coalesce(sum(label), 0) FROM read_parquet(?) WHERE fold = ?",
        [str(path), name],
    ).fetchone()
    if row is None:
        raise RuntimeError("DuckDB returned no fold count")
    rows, positives = int(row[0]), int(row[1])
    return FoldSlice(rows=rows, positives=positives, prevalence=positives / rows if rows else 0.0)


def build_folds(config: FullDataConfig, dataset: FullDataDataset, repo_root: Path) -> FoldManifest:
    """Materialize strict chronological folds without splitting equal timestamps."""
    source = feature_path(config, dataset, repo_root)
    if not source.is_file():
        raise FileNotFoundError(f"feature stage is missing for {dataset.source}")
    output = folded_path(config, dataset, repo_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_suffix(".staging.parquet")
    staging.unlink(missing_ok=True)
    train_fraction, calibration_fraction, _holdout_fraction = config.folds.exact()
    tuning_fraction = Fraction(config.calibration_split.tuning)
    with duckdb.connect() as connection:
        total_row = connection.execute(
            "SELECT count(*) FROM read_parquet(?)", [str(source)]
        ).fetchone()
        if total_row is None:
            raise RuntimeError("DuckDB returned no feature-row count")
        total = int(total_row[0])
        train_target = int(total * train_fraction)
        middle_end_target = int(total * (train_fraction + calibration_fraction))
        tuning_target = train_target + int((middle_end_target - train_target) * tuning_fraction)
        train_end = _cohort_boundary(connection, source, train_target)
        tuning_end = _cohort_boundary(connection, source, tuning_target)
        calibration_end = _cohort_boundary(connection, source, middle_end_target)
        connection.execute(
            f"""
            COPY (
              SELECT *, CASE
                WHEN cohort_key <= ? THEN 'train'
                WHEN cohort_key <= ? THEN 'tuning'
                WHEN cohort_key <= ? THEN 'calibration'
                ELSE 'holdout'
              END AS fold
              FROM read_parquet(?) ORDER BY cohort_key, row_id
            ) TO {_sql_text(str(staging))}
              (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 65536)
            """,
            [train_end, tuning_end, calibration_end, str(source)],
        )
        staging.replace(output)
        counts = FoldCounts(**{name: _slice(connection, output, name) for name in _FOLD_NAMES})
    manifest = FoldManifest(
        candidate=dataset.candidate,
        source=dataset.source,
        fractions=(config.folds.train, config.folds.calibration, config.folds.holdout),
        calibration_fractions=(
            config.calibration_split.tuning,
            config.calibration_split.calibration,
        ),
        boundary_epochs=(train_end, tuning_end, calibration_end),
        counts=counts,
    )
    atomic_write_model(fold_manifest_path(config, dataset, repo_root), manifest)
    return manifest


def load_fold_manifest(
    config: FullDataConfig, dataset: FullDataDataset, repo_root: Path
) -> FoldManifest:
    """Load a validated temporal fold manifest."""
    return FoldManifest.model_validate_json(
        fold_manifest_path(config, dataset, repo_root).read_text(encoding="utf-8")
    )

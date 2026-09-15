"""Summary: Mandatory sampled parity between DuckDB features and the live feature builder.

Key classes:
- ParityResult: feature-artifact binding and maximum sampled error.

Key functions:
- validate_feature_parity: compare sampled rows against build_feature_matrix within 1e-9.
- parity_path: resolve one mandatory parity marker.
- load_parity_result: load the mandatory training prerequisite marker.

Notes:
- Sample histories are fetched per target, keeping real-data parity memory bounded.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from fraudlens_ml.scoring import FEATURE_NAMES
from lib.aml_fraud import IBM_AML, build_feature_matrix
from lib.fulldata.config import FullDataConfig, FullDataDataset
from lib.fulldata.features import feature_path, load_feature_build
from lib.fulldata.ingest import ingested_scan
from lib.study import atomic_write_model

_PARITY_ABSOLUTE_TOLERANCE = 1e-9


class ParityResult(BaseModel):
    """Successful parity check bound to one immutable feature artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: str = Field(..., description="Configured candidate identity.")
    source: str = Field(..., description="Fetch-registry source identity.")
    sampled_rows: int = Field(..., gt=0, description="Rows checked against live semantics.")
    max_absolute_error: float = Field(..., ge=0.0, description="Largest feature difference.")
    tolerance: float = Field(..., gt=0.0, description="Required absolute tolerance.")
    feature_sha256: str = Field(
        ..., pattern=r"^[0-9a-f]{64}$", description="Checked artifact hash."
    )
    passed: bool = Field(..., description="Whether every sampled feature met tolerance.")


def parity_path(config: FullDataConfig, dataset: FullDataDataset, repo_root: Path) -> Path:
    """Resolve one source's mandatory parity marker."""
    return repo_root / config.paths.work_dir / "parity" / f"{dataset.source}.json"


def _reference_frame(rows: pd.DataFrame) -> pd.DataFrame:
    """Map typed local rows back to the public IBM columns consumed by the live builder."""
    return pd.DataFrame(
        {
            "Timestamp": rows["occurred_at"],
            "From Bank": "",
            "Account": rows["origin_account"],
            "To Bank": "",
            "Account.1": rows["dest_account"],
            "Amount Paid": rows["amount"].astype(str),
            "Payment Currency": rows["payment_currency"],
            "Payment Format": rows["payment_format"],
            "Is Laundering": rows["label"],
        }
    )


def validate_feature_parity(
    config: FullDataConfig,
    dataset: FullDataDataset,
    repo_root: Path,
    *,
    sample_rows: int = 100,
) -> ParityResult:
    """Compare deterministic sampled SQL rows with the production-compatible Python builder."""
    if sample_rows <= 0:
        raise ValueError("parity sample_rows must be positive")
    features = feature_path(config, dataset, repo_root)
    ingested = ingested_scan(config, dataset, repo_root)
    build = load_feature_build(config, dataset, repo_root)
    with duckdb.connect() as connection:
        targets = connection.execute(
            "SELECT * FROM read_parquet(?) ORDER BY hash(row_id, ?) LIMIT ?",
            [str(features), config.training.seed, sample_rows],
        ).fetch_df()
        if targets.empty:
            raise ValueError("parity requires at least one feature row")
        maximum = 0.0
        for target in targets.itertuples(index=False):
            history = connection.execute(
                """
                SELECT * FROM read_parquet(?, hive_partitioning=false)
                WHERE occurred_at >= ? - INTERVAL 24 HOURS AND occurred_at <= ?
                  AND (origin_account IN (?, ?) OR dest_account IN (?, ?))
                ORDER BY occurred_at, row_id
                """,
                [
                    ingested,
                    target.occurred_at,
                    target.occurred_at,
                    target.origin_account,
                    target.dest_account,
                    target.origin_account,
                    target.dest_account,
                ],
            ).fetch_df()
            positions = np.flatnonzero(history["row_id"].to_numpy() == target.row_id)
            if positions.shape[0] != 1:
                raise RuntimeError("parity target row is not uniquely present in typed history")
            expected, _labels = build_feature_matrix(
                _reference_frame(history),
                IBM_AML,
                history_max=config.features.history_max,
            )
            observed = np.asarray([getattr(target, name) for name in FEATURE_NAMES], dtype=float)
            error = float(np.max(np.abs(expected[int(positions[0])] - observed)))
            maximum = max(maximum, error)
    passed = maximum <= _PARITY_ABSOLUTE_TOLERANCE
    result = ParityResult(
        candidate=dataset.candidate,
        source=dataset.source,
        sampled_rows=len(targets),
        max_absolute_error=maximum,
        tolerance=_PARITY_ABSOLUTE_TOLERANCE,
        feature_sha256=build.parquet_sha256,
        passed=passed,
    )
    if not passed:
        raise ValueError(
            f"{dataset.source}: feature parity failed (max error {maximum:.12g}, "
            f"tolerance {_PARITY_ABSOLUTE_TOLERANCE})"
        )
    atomic_write_model(parity_path(config, dataset, repo_root), result)
    return result


def load_parity_result(
    config: FullDataConfig, dataset: FullDataDataset, repo_root: Path
) -> ParityResult:
    """Load and validate the mandatory parity marker against the current feature artifact."""
    result = ParityResult.model_validate_json(
        parity_path(config, dataset, repo_root).read_text(encoding="utf-8")
    )
    build = load_feature_build(config, dataset, repo_root)
    if not result.passed or result.feature_sha256 != build.parquet_sha256:
        raise ValueError(f"{dataset.source}: parity marker is absent, failed, or stale")
    return result

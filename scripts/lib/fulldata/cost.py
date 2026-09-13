"""Summary: Hash-bound persistence for full-data pilot cost projections.

Key classes:
- CostProjectionRecord: pilot inputs, allocation, protocol hash, and deterministic projection.

Key functions:
- save_cost_projection: atomically persist one estimate for later run binding.
- load_cost_projection: validate and return the current protocol's estimate when present.

Notes:
- Admission remains under experiment governance; this module stores evidence but creates nothing.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from lib.experiments.budget import Projection
from lib.fulldata.config import FullDataConfig
from lib.study import atomic_write_model


class CostProjectionRecord(BaseModel):
    """One pilot-derived estimate bound to the full-data protocol and allocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    config_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="Protocol hash.")
    allocation: str = Field(..., min_length=1, description="Experiment budget allocation.")
    rate_key: str = Field(..., min_length=1, description="Rate provenance key.")
    pilot_hours: Decimal = Field(..., ge=0, description="Observed pilot compute hours.")
    pilot_rows: Decimal = Field(..., gt=0, description="Rows completed by the pilot.")
    target_rows: Decimal = Field(..., gt=0, description="Rows in the projected full run.")
    projection: Projection = Field(..., description="Deterministic projected hours and cost.")


def _projection_path(config: FullDataConfig, repo_root: Path) -> Path:
    return repo_root / config.paths.work_dir / "cost-projection.json"


def save_cost_projection(  # noqa: PLR0913 - explicit projection provenance contract
    config: FullDataConfig,
    repo_root: Path,
    *,
    rate_key: str,
    pilot_hours: Decimal,
    pilot_rows: Decimal,
    target_rows: Decimal,
    projection: Projection,
) -> CostProjectionRecord:
    """Atomically bind pilot inputs and their projection to the active protocol."""
    record = CostProjectionRecord(
        config_sha256=config.config_sha256,
        allocation=config.budget.allocation,
        rate_key=rate_key,
        pilot_hours=pilot_hours,
        pilot_rows=pilot_rows,
        target_rows=target_rows,
        projection=projection,
    )
    atomic_write_model(_projection_path(config, repo_root), record)
    return record


def load_cost_projection(config: FullDataConfig, repo_root: Path) -> CostProjectionRecord | None:
    """Load the current protocol's estimate, failing closed on a stale binding."""
    path = _projection_path(config, repo_root)
    if not path.is_file():
        return None
    record = CostProjectionRecord.model_validate_json(path.read_text(encoding="utf-8"))
    if (
        record.config_sha256 != config.config_sha256
        or record.allocation != config.budget.allocation
    ):
        raise ValueError("cost projection does not match the active full-data protocol")
    return record

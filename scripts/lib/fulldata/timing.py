"""Summary: Atomic per-source duration and peak-memory ledger for resumable full-data runs.

Key classes:
- StageMeasurements: validated duration and peak RSS for successful stages.

Key functions:
- record_stage_duration: atomically replace one successful stage measurement.
- load_stage_measurements: load measurements or return an empty validated ledger.

Notes:
- Failed stages never call the recorder, so partial attempts cannot appear as completed timing.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from lib.fulldata.config import FullDataConfig, FullDataDataset
from lib.fulldata.manifest import peak_rss_mib
from lib.study import atomic_write_model


class StageMeasurements(BaseModel):
    """Successful stage durations and process high-water memory for one source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(..., description="Fetch-registry source identity.")
    durations_seconds: dict[str, float] = Field(
        default_factory=dict, description="Successful stage name to elapsed seconds."
    )
    peak_rss_mib: dict[str, float] = Field(
        default_factory=dict, description="Successful stage name to process peak resident MiB."
    )


def _timing_path(config: FullDataConfig, dataset: FullDataDataset, repo_root: Path) -> Path:
    return repo_root / config.paths.work_dir / "timings" / f"{dataset.source}.json"


def load_stage_measurements(
    config: FullDataConfig, dataset: FullDataDataset, repo_root: Path
) -> StageMeasurements:
    """Load stage measurements, or return an empty ledger before the first completed stage."""
    path = _timing_path(config, dataset, repo_root)
    if not path.is_file():
        return StageMeasurements(source=dataset.source)
    return StageMeasurements.model_validate_json(path.read_text(encoding="utf-8"))


def record_stage_duration(
    config: FullDataConfig,
    dataset: FullDataDataset,
    repo_root: Path,
    stage: str,
    duration_seconds: float,
) -> StageMeasurements:
    """Replace one completed stage duration and RSS while retaining other stages."""
    if not stage or duration_seconds < 0.0:
        raise ValueError("stage timing requires a name and non-negative duration")
    current = load_stage_measurements(config, dataset, repo_root)
    durations = dict(current.durations_seconds)
    durations[stage] = duration_seconds
    memory = dict(current.peak_rss_mib)
    memory[stage] = peak_rss_mib()
    result = StageMeasurements(
        source=dataset.source, durations_seconds=durations, peak_rss_mib=memory
    )
    atomic_write_model(_timing_path(config, dataset, repo_root), result)
    return result

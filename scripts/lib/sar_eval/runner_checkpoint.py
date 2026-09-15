"""Summary: Path binding and resumable checkpoint persistence for SAR API evaluation.

Key classes:
- (none)

Key functions:
- checkpoint_path: resolve the run-bound checkpoint path.
- load_checkpoint: initialize or validate resumable state.
- write_checkpoint: atomically persist resumable state.

Notes:
- Resume authority may increase but never lower the prior hard cap.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from lib.sar_eval.config import SarEvalConfig
from lib.sar_eval.runner_contracts import ApiRunCheckpoint
from lib.sar_eval.runner_transport import atomic_write
from lib.sar_eval.scenarios import ScenarioArtifact, validate_scenario_binding
from lib.study.binding import load_checkpoint_or_initialize

_REPO_ROOT = Path(__file__).resolve().parents[3]


def checkpoint_path(config: SarEvalConfig, run_id: str) -> Path:
    return _REPO_ROOT / config.paths.output_dir / run_id / "runs.checkpoint.json"


def load_checkpoint(
    path: Path,
    scenarios: ScenarioArtifact,
    config: SarEvalConfig,
    max_usd: Decimal,
) -> ApiRunCheckpoint:
    checkpoint = load_checkpoint_or_initialize(
        path,
        ApiRunCheckpoint,
        lambda: ApiRunCheckpoint(
            run_id=scenarios.run_id,
            config_sha256=scenarios.config_sha256,
            authorized_max_usd=max_usd,
        ),
    )
    if checkpoint.run_id != scenarios.run_id:
        raise ValueError("API checkpoint run id does not match the requested evaluation run")
    validate_scenario_binding(
        scenarios,
        expected_run_id=checkpoint.run_id,
        config=config,
    )
    if checkpoint.config_sha256 != scenarios.config_sha256:
        raise ValueError("API checkpoint config SHA does not match the scenario artifact")
    expected_reservation = Decimal(str(config.api.max_cost_usd_per_run))
    if any(item.amount_usd != expected_reservation for item in checkpoint.reservations):
        raise ValueError("API checkpoint reservation does not match the loaded protocol")
    if max_usd < checkpoint.authorized_max_usd:
        raise ValueError("resumed API run cannot lower its previously authorized USD cap")
    if max_usd > checkpoint.authorized_max_usd:
        return checkpoint.model_copy(update={"authorized_max_usd": max_usd})
    return checkpoint


def write_checkpoint(path: Path, checkpoint: ApiRunCheckpoint) -> None:
    atomic_write(path, checkpoint)

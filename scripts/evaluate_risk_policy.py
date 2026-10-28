"""Summary: Reproducible, provider-free comparison of risk-ranking policies.

Key classes:
- (none)

Key functions:
- main: evaluate selected frozen source/model comparisons from the committed configuration.

Notes:
- Reads ignored synthetic feature inputs; writes ignored aggregate evidence and caches only.
- Does not mutate serving defaults, a database, model metadata, or any cloud resource.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from lib.risk_study.evaluate import evaluate_run
from lib.risk_study.models import load_config
from lib.risk_study.prepare import prepare_inputs

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/risk-ranking.yaml"


def main() -> int:
    """Run a declared comparison without fitting anything on its final evaluation labels."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--prepare", action="store_true", help="Recreate local synthetic feature inputs."
    )
    parser.add_argument(
        "--run", action="append", help="Configured run name; repeat or omit for all."
    )
    args = parser.parse_args()
    config = load_config(args.config)
    names = set(args.run or [run.name for run in config.runs])
    if names - {run.name for run in config.runs}:
        parser.error("unknown run name")
    if args.prepare:
        prepare_inputs(ROOT, config, [run for run in config.runs if run.name in names])
        return 0
    for run in config.runs:
        if run.name in names:
            evaluate_run(ROOT, config, run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

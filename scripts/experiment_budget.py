"""Summary: Operator CLI for FraudLens experiment cost estimation, allocation
admission, and ledger reconciliation. Estimates are derived from pilot throughput
and committed rate provenance; ledger checks are read-only and print no secrets.

Key classes:
- (none)

Key functions:
- main: dispatch estimate, admit, and ledger-check commands.

Notes:
- An admitted estimate is necessary but never sufficient permission for a paid run.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from lib.experiments.budget import (
    DEFAULT_BUDGET_CONFIG,
    DEFAULT_LEDGER,
    DEFAULT_REPORTS,
    BudgetConfig,
    PilotMeasurement,
    Projection,
    admit,
    check_ledger,
    load_budget_config,
    load_ledger,
    project_cost,
)


def _projection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--rate", required=True, help="Rate key from budget.yaml")
    parser.add_argument("--pilot-hours", required=True, type=Decimal)
    parser.add_argument("--pilot-units", required=True, type=Decimal)
    parser.add_argument("--target-units", required=True, type=Decimal)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bound FraudLens experiment spending.")
    parser.add_argument("--config", type=Path, default=DEFAULT_BUDGET_CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)
    estimate_parser = subparsers.add_parser("estimate")
    _projection_args(estimate_parser)
    admit_parser = subparsers.add_parser("admit")
    _projection_args(admit_parser)
    admit_parser.add_argument("--allocation", required=True)
    ledger_parser = subparsers.add_parser("ledger-check")
    ledger_parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ledger_parser.add_argument("--reports", type=Path, default=DEFAULT_REPORTS)
    return parser


def _project(args: argparse.Namespace, config: BudgetConfig) -> Projection:
    rate = config.rates[args.rate]
    return project_cost(
        [
            PilotMeasurement(
                rate_key=args.rate,
                completed_units=args.pilot_units,
                target_units=args.target_units,
                elapsed_hours=args.pilot_hours,
                hourly_rate_usd=rate.hourly_rate_usd,
            )
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Execute a budget subcommand and return a shell-friendly status."""
    args = _build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    config = load_budget_config(repo_root, args.config)
    if args.command == "ledger-check":
        ledger_path = args.ledger if args.ledger.is_absolute() else repo_root / args.ledger
        reports_path = args.reports if args.reports.is_absolute() else repo_root / args.reports
        errors = check_ledger(config, load_ledger(ledger_path), reports_path)
        for error in errors:
            print(error)
        if errors:
            print(f"ledger-check FAILED: {len(errors)} violation(s)")
            return 1
        print("ledger-check OK: report coverage and the $75 ceiling reconcile")
        return 0
    projection = _project(args, config)
    if args.command == "estimate":
        print(json.dumps(projection.model_dump(mode="json"), sort_keys=True))
        return 0
    if args.allocation not in config.allocations:
        raise SystemExit(f"unknown allocation: {args.allocation}")
    decision = admit(
        projection,
        config.allocations[args.allocation],
        admission_margin=config.admission_margin,
    )
    print(json.dumps(decision.model_dump(mode="json"), sort_keys=True))
    return 0 if decision.admitted else 1


if __name__ == "__main__":
    raise SystemExit(main())

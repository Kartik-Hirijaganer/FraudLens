"""Summary: Stage-oriented CLI for the resumable IBM full-data training pipeline.

Key classes:
- (none)

Key functions:
- main: verify, ingest, feature, parity, fold, train, evaluate, report, publish, or estimate.

Notes:
- The CLI is keyless; paid Azure orchestration and database registration happen elsewhere.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from lib.experiments.budget import PilotMeasurement, load_budget_config, project_cost
from lib.fulldata.config import DEFAULT_FULLDATA_CONFIG, FullDataConfig, load_fulldata_config
from lib.fulldata.cost import save_cost_projection
from lib.fulldata.features import build_features
from lib.fulldata.folds import build_folds
from lib.fulldata.ingest import ingest_dataset, verify_dataset
from lib.fulldata.parity import validate_feature_parity
from lib.fulldata.publish import publish_report, validate_published_artifacts
from lib.fulldata.report import current_run_id, evaluate_run, run_directory, write_report
from lib.fulldata.timing import load_stage_measurements, record_stage_duration
from lib.fulldata.train import train_candidate

REPO_ROOT = Path(__file__).resolve().parents[1]
_DOCS_REPORT = REPO_ROOT / "docs/reference/benchmarks/ibm-full-data-training.json"
_FRONTEND_REPORT = REPO_ROOT / "frontend/src/data/ibm-full-data-training.json"
_CANDIDATE_COMMANDS = {"ingest", "features", "parity", "folds", "train"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the checkpointed IBM full-data pipeline.")
    parser.add_argument("--config", type=Path, default=DEFAULT_FULLDATA_CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--candidate")
    for name in sorted(_CANDIDATE_COMMANDS):
        command = subparsers.add_parser(name)
        command.add_argument("--candidate", required=True)
        if name == "ingest":
            command.add_argument("--rows", type=int)
        if name == "parity":
            command.add_argument("--sample-rows", type=int, default=100)
    subparsers.add_parser("evaluate")
    subparsers.add_parser("report")
    subparsers.add_parser("publish")
    subparsers.add_parser("validate")
    pilot = subparsers.add_parser("pilot")
    pilot.add_argument("--candidate", required=True)
    pilot.add_argument("--rows", type=int, required=True)
    estimate = subparsers.add_parser("estimate")
    estimate.add_argument("--rate", required=True)
    estimate.add_argument("--pilot-hours", type=Decimal, required=True)
    estimate.add_argument("--pilot-rows", type=Decimal, required=True)
    estimate.add_argument("--target-rows", type=Decimal, required=True)
    return parser


def _print_model(value: object) -> None:
    payload = value.model_dump(mode="json", by_alias=True)  # type: ignore[attr-defined]
    print(json.dumps(payload, sort_keys=True))


def _candidate_stage(args: argparse.Namespace, config: FullDataConfig) -> object:
    dataset = config.dataset(args.candidate)
    started = time.monotonic()
    result: object
    if args.command == "ingest":
        result = ingest_dataset(config, dataset, REPO_ROOT, row_limit=args.rows)
    elif args.command == "features":
        result = build_features(config, dataset, REPO_ROOT)
    elif args.command == "parity":
        result = validate_feature_parity(config, dataset, REPO_ROOT, sample_rows=args.sample_rows)
    elif args.command == "folds":
        result = build_folds(config, dataset, REPO_ROOT)
    else:
        result = train_candidate(config, dataset, REPO_ROOT)
    record_stage_duration(config, dataset, REPO_ROOT, args.command, time.monotonic() - started)
    return result


def _pilot(args: argparse.Namespace, config: FullDataConfig) -> int:
    if args.rows not in config.pilot.row_targets:
        raise ValueError(f"pilot rows must be one of {list(config.pilot.row_targets)}")
    pilot_root = f"{config.paths.work_dir}/pilots/{args.candidate}/{args.rows}"
    pilot_paths = config.paths.model_copy(
        update={"work_dir": pilot_root, "artifacts_dir": f"{pilot_root}/artifacts"}
    )
    pilot_config = config.model_copy(update={"paths": pilot_paths})
    dataset = pilot_config.dataset(args.candidate)
    stage_calls = (
        ("ingest", lambda: ingest_dataset(pilot_config, dataset, REPO_ROOT, row_limit=args.rows)),
        ("features", lambda: build_features(pilot_config, dataset, REPO_ROOT)),
        ("parity", lambda: validate_feature_parity(pilot_config, dataset, REPO_ROOT)),
        ("folds", lambda: build_folds(pilot_config, dataset, REPO_ROOT)),
        ("train", lambda: train_candidate(pilot_config, dataset, REPO_ROOT)),
    )
    for name, call in stage_calls:
        started = time.monotonic()
        result = call()
        record_stage_duration(pilot_config, dataset, REPO_ROOT, name, time.monotonic() - started)
        _print_model(result)
    _print_model(load_stage_measurements(pilot_config, dataset, REPO_ROOT))
    return 0


def _estimate(args: argparse.Namespace, config: FullDataConfig) -> int:
    budget = load_budget_config(REPO_ROOT)
    rate = budget.rates[args.rate]
    projection = project_cost(
        [
            PilotMeasurement(
                rate_key=args.rate,
                completed_units=args.pilot_rows,
                target_units=args.target_rows,
                elapsed_hours=args.pilot_hours,
                hourly_rate_usd=rate.hourly_rate_usd,
            )
        ]
    )
    save_cost_projection(
        config,
        REPO_ROOT,
        rate_key=args.rate,
        pilot_hours=args.pilot_hours,
        pilot_rows=args.pilot_rows,
        target_rows=args.target_rows,
        projection=projection,
    )
    _print_model(projection)
    print(f"allocation={config.budget.allocation}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:  # noqa: PLR0911 - stage dispatcher
    """Dispatch one idempotent pipeline stage and return a shell-friendly status."""
    args = _build_parser().parse_args(argv)
    config = load_fulldata_config(args.config)
    if args.command == "verify":
        datasets = config.datasets if args.candidate is None else (config.dataset(args.candidate),)
        for dataset in datasets:
            _print_model(verify_dataset(config, dataset, REPO_ROOT))
        return 0
    if args.command in _CANDIDATE_COMMANDS:
        _print_model(_candidate_stage(args, config))
        return 0
    if args.command == "pilot":
        return _pilot(args, config)
    if args.command == "estimate":
        return _estimate(args, config)
    if args.command == "evaluate":
        _print_model(evaluate_run(config, REPO_ROOT))
        return 0
    if args.command == "report":
        _print_model(write_report(config, REPO_ROOT))
        return 0
    if args.command == "validate":
        _print_model(validate_published_artifacts(_DOCS_REPORT, _FRONTEND_REPORT, config))
        return 0
    run_id = current_run_id(config, REPO_ROOT)
    report = write_report(config, REPO_ROOT)
    if report.run_id != run_id or run_directory(config, REPO_ROOT, run_id).name != run_id:
        raise RuntimeError("current full-data run pointer is inconsistent")
    _print_model(publish_report(report, config, REPO_ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

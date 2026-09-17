"""Summary: CLI for the governed RunPod Secure Cloud vLLM benchmark host.

Key classes:
- (none)

Key functions:
- main: plan, create, inspect, connect, sync, start, stop, export, delete, or verify cleanup.

Notes:
- A cascade run provisions one Pod per endpoint role, so every command takes `--role`; omitting it
  addresses the single-endpoint session the raw quantization comparison uses.
- Plan/status/SSH/export/verify-clean are non-billable lifecycle reads or data transfer.
- Create/sync/start/stop/delete require command-specific explicit confirmation flags.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from lib.experiments.budget import DEFAULT_BUDGET_CONFIG, load_budget_config
from lib.runpod_gpu.api import RunpodApi, api_key_from_env, read_gpu_inventory
from lib.runpod_gpu.config import DEFAULT_CONFIG, RunpodGpuConfig, load_config
from lib.runpod_gpu.lifecycle import (
    create_session,
    delete_session,
    start_session,
    stop_session,
    verify_clean,
)
from lib.runpod_gpu.models import RunpodPlan
from lib.runpod_gpu.planning import build_plan, read_public_key
from lib.runpod_gpu.session import pod_status, ssh_argv
from lib.runpod_gpu.transfer import export_session, sync_session
from lib.vllm_bench.config import DEFAULT_VLLM_BENCH_CONFIG
from lib.vllm_bench.config import load_config as load_vllm_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run", required=True, help="vllm-bench-<16 lowercase hex>")
    parser.add_argument("--role", default=None, help="Endpoint role for a two-endpoint cascade run")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--budget-config", type=Path, default=DEFAULT_BUDGET_CONFIG)
    parser.add_argument("--vllm-config", type=Path, default=DEFAULT_VLLM_BENCH_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan")
    _run_argument(plan)

    create = commands.add_parser("create")
    _run_argument(create)
    create.add_argument("--confirm-create", action="store_true")

    status = commands.add_parser("status")
    _run_argument(status)

    ssh = commands.add_parser("ssh")
    _run_argument(ssh)

    sync = commands.add_parser("sync")
    _run_argument(sync)
    sync.add_argument("--cases", type=Path, required=True)
    sync.add_argument("--confirm-sync", action="store_true")

    start = commands.add_parser("start")
    _run_argument(start)
    start.add_argument("--confirm-start", action="store_true")

    stop = commands.add_parser("stop")
    _run_argument(stop)
    stop.add_argument("--confirm-stop", action="store_true")

    export = commands.add_parser("export")
    _run_argument(export)

    delete = commands.add_parser("delete")
    _run_argument(delete)
    delete.add_argument("--confirm-delete", action="store_true")

    clean = commands.add_parser("verify-clean")
    _run_argument(clean)
    return parser


def _print_model(model: BaseModel) -> None:
    payload = model.model_dump(mode="json")
    print(json.dumps(payload, sort_keys=True))


def _client(config: RunpodGpuConfig) -> RunpodApi:
    return RunpodApi(base_url=str(config.api_base_url), api_key=api_key_from_env(config))


def _plan(args: argparse.Namespace, config: RunpodGpuConfig) -> RunpodPlan:
    budget = load_budget_config(REPO_ROOT, args.budget_config)
    return build_plan(
        config,
        budget,
        read_gpu_inventory(),
        run_id=args.run,
        role=args.role,
        repo_root=REPO_ROOT,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch one RunPod operation and return a shell-friendly status."""
    args = _parser().parse_args(argv)
    config = load_config(args.config)
    with _client(config) as api:
        if args.command == "plan":
            _print_model(_plan(args, config))
        elif args.command == "create":
            plan = _plan(args, config)
            _print_model(
                create_session(
                    config,
                    api,
                    plan,
                    public_key=read_public_key(config),
                    repo_root=REPO_ROOT,
                    confirmed=args.confirm_create,
                )
            )
        elif args.command == "status":
            _print_model(
                pod_status(config, api, run_id=args.run, role=args.role, repo_root=REPO_ROOT)
            )
        elif args.command == "ssh":
            status = pod_status(config, api, run_id=args.run, role=args.role, repo_root=REPO_ROOT)
            command = ssh_argv(config, status)
            os.execvp(command[0], command)
        elif args.command == "sync":
            _print_model(
                sync_session(
                    config,
                    load_vllm_config(args.vllm_config),
                    api,
                    run_id=args.run,
                    role=args.role,
                    cases_path=args.cases,
                    repo_root=REPO_ROOT,
                    confirmed=args.confirm_sync,
                )
            )
        elif args.command == "start":
            start_session(
                config,
                api,
                run_id=args.run,
                role=args.role,
                repo_root=REPO_ROOT,
                confirmed=args.confirm_start,
            )
        elif args.command == "stop":
            stop_session(
                config,
                api,
                run_id=args.run,
                role=args.role,
                repo_root=REPO_ROOT,
                confirmed=args.confirm_stop,
            )
        elif args.command == "export":
            _print_model(
                export_session(config, api, run_id=args.run, role=args.role, repo_root=REPO_ROOT)
            )
        elif args.command == "delete":
            _print_model(
                delete_session(
                    config,
                    api,
                    run_id=args.run,
                    role=args.role,
                    repo_root=REPO_ROOT,
                    confirmed=args.confirm_delete,
                )
            )
        else:
            _print_model(verify_clean(config, api, run_id=args.run, role=args.role))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

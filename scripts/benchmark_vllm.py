"""Summary: CLI for deterministic BF16-versus-AWQ vLLM SAR benchmarking.

Key classes:
- (none)

Key functions:
- main: build cases, manage servers, run arms or cascade scenarios, report, or publish.

Notes:
- IBM full-profile case generation fails closed until Phase 6 application artifacts exist.
- GPU and release-upload actions are explicit commands; validation is provider-free and read-only.
- `cascade-pilot` is free: it composes the gated cascade from an already-persisted run, contacts
  no provider, creates no resource, and is the admission projection a paid cascade run needs.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx

from fraudlens_backend.sar.factory import load_sar_llm_config
from fraudlens_backend.sar.quality_gate import SarQualityGate, load_sar_gate_policy
from lib.experiments.budget import DEFAULT_LEDGER, load_budget_config, load_ledger
from lib.study import (
    atomic_write_model,
    canonical_json,
    derive_run_id,
    resolve_git_commit,
    sha256_hex,
)
from lib.vllm_bench.cascade_load import role_command_prefix, role_telemetry, run_scenario
from lib.vllm_bench.cascade_pilot import CascadeReplayPilot, project_replay_pilot
from lib.vllm_bench.cases_fixture import build_fixture_cases
from lib.vllm_bench.cases_ibm import build_ibm_cases
from lib.vllm_bench.client import OpenAiCompatibleStreamClient
from lib.vllm_bench.config import (
    DEFAULT_VLLM_BENCH_CONFIG,
    ArmName,
    CaseSource,
    VllmBenchConfig,
    load_config,
    resolve_profile,
)
from lib.vllm_bench.e2e import run_e2e
from lib.vllm_bench.load import run_arm
from lib.vllm_bench.publish import publish_report, validate_published_artifacts
from lib.vllm_bench.report import build_report, load_report, write_report
from lib.vllm_bench.scenario_runtime import build_scenario_drafter
from lib.vllm_bench.server import (
    image_digest,
    read_startup_logs,
    render_docker_argv,
    serve,
    server_provenance,
    stop,
)
from lib.vllm_bench.state import (
    BenchmarkCase,
    CaseReleaseManifest,
    load_case_bundle,
    load_run,
    write_case_bundle,
)
from lib.vllm_bench.telemetry import build_sampler, query_host_facts

REPO_ROOT = Path(__file__).resolve().parents[1]
_DOCS_REPORT = REPO_ROOT / "docs/reference/benchmarks/vllm-awq-sar-benchmark.json"
_FRONTEND_REPORT = REPO_ROOT / "frontend/src/data/vllm-awq-sar-benchmark.json"
_DOCS_PILOT = REPO_ROOT / "docs/reference/benchmarks/vllm-cascade-replay-pilot.json"
_RUN_PROFILES = ("smoke", "development", "full")


def _paths(config: VllmBenchConfig, *, profile: str, source: str) -> tuple[Path, Path]:
    """Return the stable case path and scratch root for one configuration."""
    root = REPO_ROOT / config.paths.output_dir
    return root / f"cases-{source}-{profile}.json", root


def _parser() -> argparse.ArgumentParser:
    """Build the stage-oriented command parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_VLLM_BENCH_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)

    cases = commands.add_parser("cases")
    cases.add_argument("--profile", choices=("smoke", "full"), required=True)
    cases.add_argument("--source", choices=("sar-eval", "ibm-final-test"), required=True)

    release = commands.add_parser("cases-release")
    release.add_argument("--run", required=True)
    release.add_argument("--confirm-upload", action="store_true")

    for name in ("serve", "run"):
        command = commands.add_parser(name)
        command.add_argument("--arm", choices=("bf16", "awq"), required=True)
        if name == "run":
            command.add_argument("--profile", choices=_RUN_PROFILES, required=True)
            command.add_argument("--source", choices=("sar-eval", "ibm-final-test"), required=True)
            command.add_argument("--cases", type=Path)
            command.add_argument("--run")
            command.add_argument("--host", required=True)
            command.add_argument(
                "--purchase-option", choices=("spot", "pay_as_you_go"), required=True
            )
    commands.add_parser("stop")

    e2e = commands.add_parser("e2e")
    e2e.add_argument("--cases", type=int, default=100)
    e2e.add_argument("--concurrency", type=int, default=4)
    e2e.add_argument("--run")
    e2e.add_argument("--model-override")

    report = commands.add_parser("report")
    report.add_argument("--run", required=True)
    report.add_argument("--cases", type=Path, required=True)

    publish = commands.add_parser("publish")
    publish.add_argument("--run", required=True)
    publish.add_argument("--allow-unmet-acceptance", action="store_true")

    scenario = commands.add_parser("run-scenario")
    scenario.add_argument("--scenario", required=True)
    scenario.add_argument("--profile", choices=_RUN_PROFILES, required=True)
    scenario.add_argument("--source", choices=("sar-eval", "ibm-final-test"), required=True)
    scenario.add_argument("--cases", type=Path)
    scenario.add_argument("--run")
    scenario.add_argument("--host", required=True)
    scenario.add_argument("--purchase-option", choices=("spot", "pay_as_you_go"), required=True)

    pilot = commands.add_parser("cascade-pilot")
    pilot.add_argument("--run")
    pilot.add_argument("--profile", default="awq-bf16")
    pilot.add_argument("--cases", type=Path)
    pilot.add_argument("--publish", action="store_true")
    commands.add_parser("validate")
    return parser


def _build_cases(config: VllmBenchConfig, profile: str, source: CaseSource) -> Path:
    """Generate one deterministic case corpus through the selected governed source."""
    artifact = (
        build_fixture_cases(config, profile=profile, repo_root=REPO_ROOT)
        if source == "sar-eval"
        else build_ibm_cases(config, profile=profile, repo_root=REPO_ROOT)
    )
    case_path, _root = _paths(config, profile=profile, source=source)
    write_case_bundle(case_path, artifact)
    print(f"vllm-bench cases OK: {case_path}")
    return case_path


def _jsonl(case: BenchmarkCase) -> bytes:
    """Serialize one case as a compact canonical JSONL record."""
    payload = case.model_dump(mode="json", by_alias=True)
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _find_run_cases(config: VllmBenchConfig, run_id: str) -> tuple[Path, str]:
    """Locate the sole local case artifact whose byte hash matches a completed run."""
    root = REPO_ROOT / config.paths.output_dir
    manifest = load_run(root / run_id / "run.json")

    def digest(path: Path) -> str:
        value = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                value.update(chunk)
        return value.hexdigest()

    matches = [
        path
        for path in root.glob("cases-*.json")
        if not path.name.endswith(".manifest.json") and digest(path) == manifest.cases_sha256
    ]
    if len(matches) != 1:
        raise ValueError("run must resolve to exactly one hash-matched local case artifact")
    return matches[0], manifest.cases_sha256


def _release_cases(
    config: VllmBenchConfig, run_id: str, *, confirm_upload: bool = False
) -> CaseReleaseManifest:
    """Create deterministic release assets and attach them to an existing GitHub release."""
    if not confirm_upload:
        raise PermissionError("release upload requires explicit --confirm-upload permission")
    case_path, run_cases_sha = _find_run_cases(config, run_id)
    artifact, artifact_sha = load_case_bundle(case_path)
    if artifact_sha != run_cases_sha:
        raise ValueError("case artifact changed after run completion")
    if artifact.profile != "full" or artifact.case_source != "ibm-final-test":
        raise ValueError("only the full IBM final-test corpus may be release-attached")
    release_dir = case_path.parent / "releases"
    release_dir.mkdir(parents=True, exist_ok=True)
    archive = release_dir / f"vllm-cases-ibm-{artifact_sha[:16]}.jsonl.gz"
    with tempfile.NamedTemporaryFile(dir=release_dir, delete=False) as staging:
        staging_path = Path(staging.name)
        with gzip.GzipFile(filename="", mode="wb", fileobj=staging, mtime=0) as compressed:
            for case in artifact.cases:
                compressed.write(_jsonl(case))
    os.replace(staging_path, archive)
    manifest = CaseReleaseManifest(
        archive_name=archive.name,
        archive_sha256=sha256_hex(archive.read_bytes()),
        artifact_sha256=artifact_sha,
        config_sha256=artifact.config_sha256,
        upstream_sha256=artifact.upstream_sha256,
        case_source=artifact.case_source,
        profile="full",
        cases=len(artifact.cases),
        measured_cases=sum(case.case_set == "measured" for case in artifact.cases),
        license=config.cases.license,
        attribution=config.cases.attribution,
        source_url=config.cases.source_url,
    )
    manifest_path = archive.with_suffix(".manifest.json")
    atomic_write_model(manifest_path, manifest)
    subprocess.run(
        ("gh", "release", "upload", run_id, str(archive), str(manifest_path)),
        cwd=REPO_ROOT,
        check=True,
    )
    return manifest


async def _run(args: argparse.Namespace, config: VllmBenchConfig) -> None:
    """Execute or resume one local/remote arm against an already running endpoint."""
    api_key = os.environ.get(config.server.api_key_env, "")
    if not api_key.strip():
        raise ValueError(f"{config.server.api_key_env} is required")
    default_case_path, root = _paths(config, profile=args.profile, source=args.source)
    case_path = getattr(args, "cases", None) or default_case_path
    artifact, cases_sha = load_case_bundle(case_path)
    if (
        artifact.profile != args.profile
        or artifact.case_source != args.source
        or artifact.config_sha256 != config.config_sha256
    ):
        raise ValueError("case artifact identity does not match the requested protocol")
    run_id = args.run or derive_run_id(
        "vllm-bench",
        f"{config.config_sha256}:{cases_sha}:{args.profile}",
    )
    logs = read_startup_logs(config, repo_root=REPO_ROOT)
    digest = os.environ.get(config.server.image_digest_env) or image_digest(config)
    provenance = server_provenance(
        config,
        arm=args.arm,
        host_key=args.host,
        purchase_option=args.purchase_option,
        startup_logs=logs,
        digest=digest,
    )
    base_url = os.environ.get(config.server.base_url_env, config.server.base_url)
    client = OpenAiCompatibleStreamClient(
        base_url=base_url,
        api_key=api_key,
        model=config.arms[args.arm].model,
        request=config.request,
    )
    try:
        await run_arm(
            run_path=root / run_id / "run.json",
            run_id=run_id,
            arm=args.arm,
            artifact=artifact,
            cases_sha256=cases_sha,
            config=config,
            profile=args.profile,
            provenance=provenance,
            client=client,
            sampler=build_sampler(config.telemetry),
        )
    finally:
        await client.close()
    print(f"vllm-bench run OK: {run_id} {args.arm}")


async def _run_scenario(args: argparse.Namespace, config: VllmBenchConfig) -> None:
    """Execute or resume one cascade scenario against the production quality-gated drafter."""
    selected = config.cascade.scenario(args.scenario)
    default_case_path, root = _paths(config, profile=args.profile, source=args.source)
    case_path = getattr(args, "cases", None) or default_case_path
    artifact, cases_sha = load_case_bundle(case_path)
    if artifact.config_sha256 != config.config_sha256:
        raise ValueError("case artifact identity does not match the requested protocol")
    run_id = args.run or derive_run_id(
        "vllm-bench", f"{config.config_sha256}:{cases_sha}:{args.profile}"
    )
    digest = os.environ.get(config.server.image_digest_env) or image_digest(config)
    provenance = {
        role: server_provenance(
            config,
            arm=cast(ArmName, config.cascade.endpoints[role].arm),
            host_key=args.host,
            purchase_option=args.purchase_option,
            startup_logs=read_startup_logs(
                config,
                repo_root=REPO_ROOT,
                command_prefix=role_command_prefix(config, role),
            ),
            digest=digest,
            gpu=query_host_facts(role_command_prefix(config, role)),
        )
        for role in selected.endpoints
    }
    await run_scenario(
        run_path=root / run_id / "run.json",
        run_id=run_id,
        scenario=selected,
        artifact=artifact,
        cases_sha256=cases_sha,
        config=config,
        profile=args.profile,
        drafter=build_scenario_drafter(config, selected.profile),
        samplers={role: build_sampler(role_telemetry(config, role)) for role in selected.endpoints},
        provenance=provenance,
        git_commit=resolve_git_commit(REPO_ROOT, injected=os.environ.get("VLLM_BENCH_GIT_COMMIT")),
    )
    print(f"vllm-bench scenario OK: {run_id} {selected.name}")


def _report(config: VllmBenchConfig, run_id: str, case_path: Path) -> None:
    """Build a typed local report from one complete two-arm matrix."""
    artifact, cases_sha = load_case_bundle(case_path)
    run_dir = REPO_ROOT / config.paths.output_dir / run_id
    manifest = load_run(run_dir / "run.json")
    if manifest.cases_sha256 != cases_sha:
        raise ValueError("run does not bind the provided case artifact")
    write_report(run_dir, build_report(manifest, artifact, config))
    print(f"vllm-bench report OK: {run_dir / 'report.json'}")


def _e2e(args: argparse.Namespace, config: VllmBenchConfig) -> None:
    """Run the functional application pass against the configured local gateway."""
    from lib.sar_eval.config import DEFAULT_SAR_EVAL_CONFIG, load_sar_eval_config  # noqa: PLC0415
    from lib.study.urls import validate_origin_url  # noqa: PLC0415

    sar_eval = load_sar_eval_config(DEFAULT_SAR_EVAL_CONFIG)
    base_url = validate_origin_url(
        os.environ.get(config.application_pass.base_url_env, config.application_pass.base_url),
        allow_http_hosts=sar_eval.api.loopback_http_hosts,
    )
    token = os.environ.get(config.application_pass.auth_token_env, "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else None
    started = datetime.now(UTC)
    run_id = args.run or derive_run_id(
        "vllm-e2e", f"{started.isoformat()}:{args.cases}:{args.concurrency}"
    )
    output_path = REPO_ROOT / config.paths.output_dir / run_id / "e2e.json"
    with httpx.Client(base_url=base_url, headers=headers, timeout=sar_eval.api.timeout_s) as client:
        result = run_e2e(
            client=client,
            config=sar_eval,
            config_bytes=DEFAULT_SAR_EVAL_CONFIG.read_bytes(),
            run_id=run_id,
            cases=args.cases,
            concurrency=args.concurrency,
            output_path=output_path,
            model_override=args.model_override,
        )
    print(
        f"vllm-bench e2e: {result.runs_completed}/{result.requested_cases} completed "
        f"through {result.llm_provider}; functional-only evidence -> {output_path}"
    )
    if result.runs_failed:
        raise RuntimeError("vLLM application pass retained failed-case evidence")


def _cascade_pilot(args: argparse.Namespace, config: VllmBenchConfig) -> CascadeReplayPilot:
    """Compose the free replay pilot from a persisted run; no provider, no resource, no spend."""
    run_id = args.run or config.cascade.replay_run_id
    case_path = args.cases or _find_run_cases(config, run_id)[0]
    artifact, cases_sha = load_case_bundle(case_path)
    run_dir = REPO_ROOT / config.paths.output_dir / run_id
    manifest = load_run(run_dir / "run.json")
    if manifest.cases_sha256 != cases_sha:
        raise ValueError("replay run does not bind the provided case artifact")
    pilot = project_replay_pilot(
        manifest=manifest,
        artifact=artifact,
        config=config,
        sar_config=load_sar_llm_config(REPO_ROOT / "config" / config.cascade.sar_config_file),
        budget=load_budget_config(REPO_ROOT),
        ledger=load_ledger(REPO_ROOT / DEFAULT_LEDGER),
        gate=SarQualityGate(load_sar_gate_policy(policy=config.cascade.replay_policy)),
        profile=args.profile,
    )
    atomic_write_model(run_dir / "cascade-pilot.json", pilot)
    if args.publish:
        atomic_write_model(_DOCS_PILOT, pilot)
    return pilot


def _validate(config: VllmBenchConfig) -> None:
    """Regenerate smoke fixtures and verify protocol, fairness, and optional publications."""
    first = build_fixture_cases(config, profile="smoke", repo_root=REPO_ROOT)
    second = build_fixture_cases(config, profile="smoke", repo_root=REPO_ROOT)
    if canonical_json(first) != canonical_json(second):
        raise ValueError("SAR-eval smoke case generation is not deterministic")
    requested, levels, warmups = resolve_profile(config, "smoke")
    counts = {
        kind: sum(case.case_set == kind for case in first.cases)
        for kind in ("measured", "warmup", "abstention")
    }
    if counts != {"measured": requested, "warmup": warmups, "abstention": 2}:
        raise ValueError("smoke case-set counts drifted")
    bf16 = render_docker_argv(config, "bf16")
    awq = render_docker_argv(config, "awq")
    if "--quantization" in bf16 or "awq_marlin" not in awq or levels != (1, 2):
        raise ValueError("server fairness or smoke load protocol drifted")
    ibm_path, _root = _paths(config, profile="full", source="ibm-final-test")
    if ibm_path.exists():
        ibm, _ibm_sha = load_case_bundle(ibm_path)
        ibm_counts = {
            kind: sum(case.case_set == kind for case in ibm.cases)
            for kind in ("measured", "development", "warmup", "abstention")
        }
        expected = {
            "measured": config.cases.count,
            "development": config.cases.dev_count,
            "warmup": config.cases.warmup_count,
            "abstention": config.cases.abstention_fixtures,
        }
        if ibm_counts != expected:
            raise ValueError("present IBM case artifact count contract drifted")
        if ibm.config_sha256 != config.config_sha256:
            superseded = {value: key for key, value in config.protocol_lineage.items()}
            protocol = superseded.get(ibm.config_sha256)
            if protocol is None:
                raise ValueError("present IBM case artifact binds an unrecorded protocol config")
            print(
                f"note: local IBM corpus belongs to superseded protocol {protocol}; "
                f"regenerate it before running {config.protocol_version}"
            )
    if _DOCS_REPORT.exists() != _FRONTEND_REPORT.exists():
        raise ValueError("published benchmark report/frontend pair is incomplete")
    if _DOCS_REPORT.exists():
        validate_published_artifacts(_DOCS_REPORT, _FRONTEND_REPORT, config)
    print("vllm-bench validation OK (provider-free smoke protocol)")


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch one benchmark stage and return a shell-friendly status."""
    args = _parser().parse_args(argv)
    config = load_config(args.config)
    if args.command == "cases":
        _build_cases(config, args.profile, args.source)
    elif args.command == "cases-release":
        print(
            canonical_json(_release_cases(config, args.run, confirm_upload=args.confirm_upload)),
            end="",
        )
    elif args.command == "serve":
        serve(config, args.arm, repo_root=REPO_ROOT)
    elif args.command == "stop":
        stop(config, repo_root=REPO_ROOT)
    elif args.command == "run":
        asyncio.run(_run(args, config))
    elif args.command == "run-scenario":
        asyncio.run(_run_scenario(args, config))
    elif args.command == "e2e":
        _e2e(args, config)
    elif args.command == "report":
        _report(config, args.run, args.cases)
    elif args.command == "cascade-pilot":
        print(canonical_json(_cascade_pilot(args, config)), end="")
    elif args.command == "publish":
        run_dir = REPO_ROOT / config.paths.output_dir / args.run
        result = publish_report(
            load_report(run_dir / "report.json"),
            config,
            REPO_ROOT,
            allow_unmet_acceptance=args.allow_unmet_acceptance,
        )
        print(canonical_json(result), end="")
    else:
        _validate(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

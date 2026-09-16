"""Summary: CLI for FraudLens Kubernetes render, local lifecycle, scaling, and durability evidence.

Key classes:
- (none)

Key functions:
- build_parser: define the bounded Phase 9 operator interface.
- main: validate config, dispatch one operation, and map safe failures to exit codes.

Notes:
- Local mutation is restricted to the exact configured kind context. AKS deploy/secret sync requires
  an explicit confirmation flag and is never invoked by the local umbrella demonstration.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

from lib.k8s_demo.cleanup import verify_kind_clean
from lib.k8s_demo.config import (
    DEFAULT_CONFIG_PATH,
    K8sDemoConfig,
    load_config,
    load_config_from_env,
)
from lib.k8s_demo.evidence import (
    DurabilityEvidence,
    HpaEvidenceReport,
    ScalingSample,
    load_evidence,
    validate_evidence,
)
from lib.k8s_demo.fault import hold_transaction_reads
from lib.k8s_demo.kind import KindOperator
from lib.k8s_demo.kubectl import CommandError, Kubectl, resolve_tool, run_command
from lib.k8s_demo.live_report import build_live_report
from lib.k8s_demo.load import SUBMITTED_PREFIX, LoadSummary, run_load, summary_from_logs
from lib.k8s_demo.render import Platform, deploy, render_load_job, render_overlay
from lib.k8s_demo.report import publish_evidence, render_markdown
from lib.k8s_demo.secrets import sync_secrets
from lib.k8s_demo.smoke import run_smoke

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = REPO_ROOT / "docs/reference/benchmarks/k8s-hpa-scaling.json"

__all__ = ["build_parser", "main"]


def _tools_check(config: K8sDemoConfig) -> None:
    """Resolve required CLIs and assert their configured versions where output is stable."""
    for name in ("docker", "kubectl", "kind", "kubeconform"):
        resolve_tool(name)
    kubectl_result = run_command([resolve_tool("kubectl"), "version", "--client", "-o", "json"])
    try:
        kubectl_version = json.loads(kubectl_result.stdout)["clientVersion"]["gitVersion"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CommandError("kubectl CLI version output is invalid") from exc
    if kubectl_version != f"v{config.kubectl_version}":
        raise CommandError("kubectl CLI version does not match config/k8s-demo.yaml")
    kind_result = run_command([resolve_tool("kind"), "version"])
    if f"v{config.kind_version}" not in kind_result.stdout:
        raise CommandError("kind CLI version does not match config/k8s-demo.yaml")
    conform_result = run_command([resolve_tool("kubeconform"), "-v"])
    if config.kubeconform_version not in (conform_result.stdout + conform_result.stderr):
        raise CommandError("kubeconform version does not match config/k8s-demo.yaml")


def _job_complete(kubectl: Kubectl) -> bool:
    """Return true once the load Job succeeded; fail immediately on a failed Job."""
    document = json.loads(kubectl.get_json("job/fraudlens-load"))
    status = document.get("status", {})
    if status.get("failed", 0):
        raise CommandError("load Job failed")
    return bool(status.get("succeeded", 0) >= 1)


def _job_logs(kubectl: Kubectl, *, check: bool = True) -> str:
    """Read load Job logs; callers can tolerate absence while its pod is pending."""
    result = kubectl.run(
        ["-n", kubectl.config.namespace, "logs", "job/fraudlens-load"], check=check
    )
    return result.stdout if result.returncode == 0 else ""


def _start_load_job(
    kubectl: Kubectl,
    manifest: str,
    *,
    platform: Platform = "kind",
    confirmed: bool = False,
) -> None:
    """Replace the prior load Job and apply one freshly rendered in-memory manifest."""
    kubectl.assert_mutation_allowed(platform=platform, confirmed=confirmed)
    kubectl.run(
        [
            "-n",
            kubectl.config.namespace,
            "delete",
            "job",
            "fraudlens-load",
            "--ignore-not-found=true",
        ]
    )
    kubectl.apply(manifest, platform=platform, confirmed=confirmed)


def _sample(kubectl: Kubectl, started: float) -> ScalingSample:
    """Capture one HPA observation relative to a monotonic start."""
    observed = kubectl.hpa_observation()
    return ScalingSample(
        elapsed_seconds=round(time.monotonic() - started),
        replicas=observed.current_replicas,
        desired_replicas=observed.desired_replicas,
        cpu_percent=observed.cpu_average_utilization,
    )


def _run_scaling(
    config: K8sDemoConfig,
    kubectl: Kubectl,
    *,
    platform: Platform = "kind",
    confirmed: bool = False,
) -> tuple[list[ScalingSample], LoadSummary, int]:
    """Run health load while sampling through full scale-out and back to minimum."""
    hpa = json.loads(kubectl.get_json("hpa/fraudlens-api"))["spec"]
    minimum = int(hpa["minReplicas"])
    maximum = int(hpa["maxReplicas"])
    load_config = config.load.model_copy(
        update=(
            {
                "mode": "authenticated",
                "target_url": f"http://{config.service}:8000/api/v1/dashboard/metrics",
                "concurrency": config.aks.load_concurrency,
                "auth_required": True,
            }
            if platform == "aks"
            else {"mode": "healthz"}
        )
    )
    load_image = kubectl.deployment_observation().image if platform == "aks" else None
    manifest = render_load_job(config, load_config, image=load_image)
    started = time.monotonic()
    samples = [_sample(kubectl, started)]
    if samples[0].replicas != minimum:
        raise CommandError("HPA proof must begin at its configured minimum replicas")
    _start_load_job(kubectl, manifest, platform=platform, confirmed=confirmed)
    reached_max = False
    load_finished_at: float | None = None
    while True:
        time.sleep(config.sample_interval_seconds)
        samples.append(_sample(kubectl, started))
        reached_max = reached_max or samples[-1].replicas >= maximum
        if _job_complete(kubectl) and load_finished_at is None:
            load_finished_at = time.monotonic()
        if reached_max and load_finished_at is not None and samples[-1].replicas <= minimum:
            break
        if not reached_max and time.monotonic() - started > config.scale_up_timeout_seconds:
            raise CommandError("HPA did not reach maximum replicas before the scale-up timeout")
        if (
            load_finished_at
            and time.monotonic() - load_finished_at > config.scale_down_timeout_seconds
        ):
            raise CommandError("HPA did not converge to minimum replicas before timeout")
    if load_finished_at is None:
        raise CommandError("health load Job did not complete")
    summary = summary_from_logs(_job_logs(kubectl))
    return samples, summary, round(load_finished_at - started)


def _run_durability(
    config: K8sDemoConfig,
    kubectl: Kubectl,
    *,
    platform: Platform = "kind",
    confirmed: bool = False,
) -> DurabilityEvidence:
    """Queue work with zero workers, start one, force-delete it, and require full recovery."""
    kubectl.assert_mutation_allowed(platform=platform, confirmed=confirmed)
    kubectl.run(["-n", config.namespace, "scale", "deployment/fraudlens-worker", "--replicas=0"])
    kubectl.run(
        [
            "-n",
            config.namespace,
            "rollout",
            "status",
            "deployment/fraudlens-worker",
            f"--timeout={config.startup_timeout_seconds}s",
        ]
    )
    durable_load = config.load.model_copy(
        update={
            "mode": "investigations",
            "target_url": f"http://{config.service}:8000",
            "duration_seconds": config.durability_timeout_seconds,
            "auth_required": platform == "aks",
        }
    )
    _start_load_job(
        kubectl,
        render_load_job(
            config,
            durable_load,
            image=(kubectl.deployment_observation().image if platform == "aks" else None),
        ),
        platform=platform,
        confirmed=confirmed,
    )
    deadline = time.monotonic() + config.startup_timeout_seconds
    submitted = 0
    while time.monotonic() < deadline:
        logs = _job_logs(kubectl, check=False)
        markers = [line for line in logs.splitlines() if line.startswith(SUBMITTED_PREFIX)]
        if markers:
            submitted = int(markers[-1].removeprefix(SUBMITTED_PREFIX))
            break
        time.sleep(1)
    if submitted != durable_load.cases:
        raise CommandError("durability Job did not submit every configured run")
    barrier = hold_transaction_reads(config, kubectl) if platform == "kind" else nullcontext()
    with barrier:
        kubectl.run(
            ["-n", config.namespace, "scale", "deployment/fraudlens-worker", "--replicas=1"]
        )
        if platform == "kind":
            claimed_worker = _wait_for_active_claim(config, kubectl)
        else:
            kubectl.wait_for(
                "deployment/fraudlens-worker",
                "Available",
                config.startup_timeout_seconds,
                platform=platform,
                confirmed=confirmed,
            )
            claimed_worker = kubectl.wait_for_worker_claim(platform=platform, confirmed=confirmed)
        kubectl.kill_worker_process(claimed_worker, platform=platform, confirmed=confirmed)
        deleted_pod = kubectl.delete_worker_pod(
            claimed_worker, platform=platform, confirmed=confirmed
        )
        kubectl.run(
            [
                "-n",
                config.namespace,
                "wait",
                f"pod/{deleted_pod}",
                "--for=delete",
                f"--timeout={config.startup_timeout_seconds}s",
            ],
            check=False,
        )
    kubectl.wait_for(
        "deployment/fraudlens-worker",
        "Available",
        config.startup_timeout_seconds,
        platform=platform,
        confirmed=confirmed,
    )
    kubectl.wait_for(
        "job/fraudlens-load",
        "Complete",
        config.durability_timeout_seconds,
        platform=platform,
        confirmed=confirmed,
    )
    summary = summary_from_logs(_job_logs(kubectl))
    return DurabilityEvidence(
        worker_pod_deleted=True,
        runs_submitted=summary.runs_submitted,
        runs_completed=summary.runs_completed,
        runs_completed_after_worker_kill=summary.runs_completed,
        runs_failed=summary.runs_failed,
        max_run_attempts=summary.max_run_attempts,
    )


def _wait_for_active_claim(config: K8sDemoConfig, kubectl: Kubectl) -> str:
    """Return the pod name from a live PostgreSQL-backed worker lease."""
    deadline = time.monotonic() + config.worker_claim_timeout_seconds
    query = "SELECT lease_owner FROM analysis_runs"
    query += " WHERE status = 'running' AND lease_expires_at > now()"
    query += " ORDER BY heartbeat_at DESC NULLS LAST LIMIT 1"
    while time.monotonic() < deadline:
        result = kubectl.run(
            [
                "-n",
                config.namespace,
                "exec",
                "deployment/postgres",
                "-c",
                "postgres",
                "--",
                "psql",
                "-U",
                config.postgres_user,
                "-d",
                config.postgres_database,
                "-tAc",
                query,
            ],
            check=False,
        )
        owner = result.stdout.strip()
        pod_name, separator, process_id = owner.rpartition("-")
        if result.returncode == 0 and separator and pod_name and process_id.isdigit():
            return pod_name
        time.sleep(config.worker_claim_poll_seconds)
    raise CommandError("worker did not acquire a running lease before the durability timeout")


def _live_report(  # noqa: PLR0913 - the report binds every measured proof component explicitly.
    config: K8sDemoConfig,
    samples: list[ScalingSample],
    load: LoadSummary,
    load_finished_seconds: int,
    durability: DurabilityEvidence,
    *,
    platform: Platform = "kind",
) -> HpaEvidenceReport:
    """Read live cluster/HPA/workload facts and assemble the immutable evidence envelope."""
    return build_live_report(
        config,
        samples,
        load,
        load_finished_seconds,
        durability,
        platform=platform,
        generated_at=datetime.now(UTC),
        config_path=DEFAULT_CONFIG_PATH,
        kubectl_factory=Kubectl,
        command_runner=run_command,
    )


def _hpa_demo(
    config: K8sDemoConfig,
    *,
    platform: Platform = "kind",
    confirmed: bool = False,
) -> HpaEvidenceReport:
    """Execute scaling and worker-kill proofs, publish evidence, and return the report."""
    kubectl = Kubectl(config)
    kubectl.assert_mutation_allowed(platform=platform, confirmed=confirmed)
    samples, load, load_finished = _run_scaling(
        config, kubectl, platform=platform, confirmed=confirmed
    )
    durability = _run_durability(config, kubectl, platform=platform, confirmed=confirmed)
    report = _live_report(config, samples, load, load_finished, durability, platform=platform)
    publish_evidence(report, root=REPO_ROOT)
    return report


def build_parser() -> argparse.ArgumentParser:
    """Build the explicit Phase 9 command surface."""
    parser = argparse.ArgumentParser(description="Operate the FraudLens Kubernetes demonstration")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in (
        "tools-check",
        "kind-up",
        "kind-down",
        "kind-load",
        "verify-clean",
    ):
        subparsers.add_parser(command)
    smoke_parser = subparsers.add_parser("smoke")
    smoke_parser.add_argument("--platform", choices=("kind", "aks"), default="kind")
    smoke_parser.add_argument("--confirm-aks", action="store_true")
    hpa_parser = subparsers.add_parser("hpa-demo")
    hpa_parser.add_argument("--platform", choices=("kind", "aks"), default="kind")
    hpa_parser.add_argument("--confirm-aks", action="store_true")
    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--platform", choices=("kind", "aks"), required=True)
    render_parser.add_argument("--image")
    render_parser.add_argument("--output", type=Path)
    render_parser.add_argument("--infisical-identity-id")
    render_parser.add_argument("--azure-managed-identity-client-id")
    render_parser.add_argument("--infisical-project-slug")
    deploy_parser = subparsers.add_parser("deploy")
    deploy_parser.add_argument("--platform", choices=("kind", "aks"), required=True)
    deploy_parser.add_argument("--image")
    deploy_parser.add_argument("--confirm-aks", action="store_true")
    deploy_parser.add_argument("--infisical-identity-id")
    deploy_parser.add_argument("--azure-managed-identity-client-id")
    deploy_parser.add_argument("--infisical-project-slug")
    subparsers.add_parser("load-in-cluster")
    validate_parser = subparsers.add_parser("evidence-validate")
    validate_parser.add_argument("--path", type=Path, default=EVIDENCE_PATH)
    render_evidence = subparsers.add_parser("evidence-render")
    render_evidence.add_argument("--path", type=Path, default=EVIDENCE_PATH)
    secrets_parser = subparsers.add_parser("secrets-sync")
    secrets_parser.add_argument("--confirm-aks", action="store_true")
    return parser


def _dispatch(args: argparse.Namespace, config: K8sDemoConfig) -> None:
    """Dispatch one validated operation."""
    if args.command == "tools-check":
        _tools_check(config)
    elif args.command == "kind-up":
        KindOperator(config).create()
    elif args.command == "kind-down":
        KindOperator(config).delete()
    elif args.command == "kind-load":
        KindOperator(config).load_image()
    elif args.command == "verify-clean":
        verify_kind_clean(config)
    elif args.command == "render":
        rendered = render_overlay(
            config,
            args.platform,
            image=args.image,
            infisical_identity_id=args.infisical_identity_id,
            azure_managed_identity_client_id=args.azure_managed_identity_client_id,
            infisical_project_slug=args.infisical_project_slug,
        ).yaml_text
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
    elif args.command == "deploy":
        deploy(
            config,
            args.platform,
            image=args.image,
            infisical_identity_id=args.infisical_identity_id,
            azure_managed_identity_client_id=args.azure_managed_identity_client_id,
            infisical_project_slug=args.infisical_project_slug,
            confirmed=args.confirm_aks,
        )
    elif args.command == "smoke":
        run_smoke(config, platform=args.platform, confirmed=args.confirm_aks)
    elif args.command == "load-in-cluster":
        summary = run_load(load_config_from_env())
        print(f"K8S_DEMO_SUMMARY={summary.model_dump_json()}", flush=True)
    elif args.command == "hpa-demo":
        report = _hpa_demo(config, platform=args.platform, confirmed=args.confirm_aks)
        print(render_markdown(report))
    elif args.command == "evidence-validate":
        report = load_evidence(args.path.read_text(encoding="utf-8"))
        validate_evidence(report)
    elif args.command == "evidence-render":
        report = load_evidence(args.path.read_text(encoding="utf-8"))
        print(render_markdown(report))
    elif args.command == "secrets-sync":
        names = sync_secrets(config, confirmed=args.confirm_aks)
        print(f"secrets-sync OK: applied {', '.join(names)} (values redacted)")


def main(argv: list[str] | None = None) -> int:
    """Validate configuration, dispatch one command, and print only safe errors."""
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        _dispatch(args, config)
    except (CommandError, OSError, ValueError) as exc:
        print(f"k8s-demo failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

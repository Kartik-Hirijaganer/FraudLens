"""Summary: Explicitly confirmed RunPod creation, start, stop, deletion, and cleanup proof.

Key classes:
- (none)

Key functions:
- create_session: create one duplicate-safe contract-checked Pod.
- start_session: restart only an identity-matched stopped Pod.
- stop_session: stop only an identity-matched running Pod.
- delete_session: delete only an identity-matched stopped Pod.
- verify_clean: prove matching Pods and independently billed network volumes are absent.

Notes:
- Every cloud mutation requires a true confirmation argument from the CLI boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from lib.runpod_gpu.api import RunpodApi
from lib.runpod_gpu.config import RunpodGpuConfig
from lib.runpod_gpu.models import CleanupEvidence, RunpodPlan, RunpodSession
from lib.runpod_gpu.planning import build_create_request
from lib.runpod_gpu.session import (
    load_session,
    matching_pods,
    state_path,
    validate_pod_contract,
    write_session,
)


def _now() -> datetime:
    return datetime.now(UTC)


def create_session(  # noqa: PLR0913 - explicit boundaries prevent ambient authority.
    config: RunpodGpuConfig,
    api: RunpodApi,
    plan: RunpodPlan,
    *,
    public_key: str,
    repo_root: Path,
    confirmed: bool,
) -> RunpodSession:
    """Create one duplicate-safe Pod and persist its identity for mandatory teardown."""
    if not confirmed:
        raise PermissionError("RunPod creation requires explicit confirmation")
    if state_path(config, repo_root, plan.run_id, plan.role).exists() or matching_pods(
        api, plan.pod_name
    ):
        raise ValueError("matching RunPod state or Pod already exists")
    pod = api.create_pod(build_create_request(config, plan, public_key=public_key))
    state = write_session(
        config,
        repo_root,
        RunpodSession(
            run_id=plan.run_id,
            role=plan.role,
            pod_id=pod.pod_id,
            pod_name=plan.pod_name,
            config_sha256=config.config_sha256,
            git_commit=plan.git_commit,
            image_digest=config.pod.image_digest,
            hourly_rate_usd=pod.cost_per_hour,
            created_at=_now(),
        ),
    )
    try:
        validate_pod_contract(
            config, pod, pod_name=plan.pod_name, expected_rate=plan.hourly_rate_usd
        )
    except ValueError:
        api.stop_pod(pod.pod_id)
        raise
    return state


def start_session(  # noqa: PLR0913 - the endpoint role is an explicit identity input.
    config: RunpodGpuConfig,
    api: RunpodApi,
    *,
    run_id: str,
    role: str | None = None,
    repo_root: Path,
    confirmed: bool,
) -> None:
    """Start a stopped Pod after a separate explicit approval."""
    if not confirmed:
        raise PermissionError("RunPod start requires explicit confirmation")
    state = load_session(config, repo_root, run_id, role)
    pod = api.get_pod(state.pod_id)
    if pod.name != state.pod_name or pod.desired_status != "EXITED":
        raise ValueError("only the matching stopped RunPod Pod may be started")
    api.start_pod(state.pod_id)


def stop_session(  # noqa: PLR0913 - the endpoint role is an explicit identity input.
    config: RunpodGpuConfig,
    api: RunpodApi,
    *,
    run_id: str,
    role: str | None = None,
    repo_root: Path,
    confirmed: bool,
) -> None:
    """Stop a running Pod after explicit approval, preserving its Pod volume."""
    if not confirmed:
        raise PermissionError("RunPod stop requires explicit confirmation")
    state = load_session(config, repo_root, run_id, role)
    pod = api.get_pod(state.pod_id)
    if pod.name != state.pod_name:
        raise ValueError("provider Pod identity does not match local session state")
    if pod.desired_status == "RUNNING":
        api.stop_pod(state.pod_id)
    elif pod.desired_status != "EXITED":
        raise ValueError("only a running or stopped Pod may be stopped")


def delete_session(  # noqa: PLR0913 - the endpoint role is an explicit identity input.
    config: RunpodGpuConfig,
    api: RunpodApi,
    *,
    run_id: str,
    role: str | None = None,
    repo_root: Path,
    confirmed: bool,
) -> RunpodSession:
    """Delete only a stopped identity-matched Pod after explicit approval."""
    if not confirmed:
        raise PermissionError("RunPod deletion requires explicit confirmation")
    state = load_session(config, repo_root, run_id, role)
    pod = api.get_pod(state.pod_id)
    if pod.name != state.pod_name or pod.desired_status != "EXITED":
        raise ValueError("RunPod Pod must be identity-matched and stopped before deletion")
    api.delete_pod(state.pod_id)
    return write_session(config, repo_root, state.model_copy(update={"deleted_at": _now()}))


def verify_clean(
    config: RunpodGpuConfig, api: RunpodApi, *, run_id: str, role: str | None = None
) -> CleanupEvidence:
    """Prove matching Pods and independently billed network volumes are absent."""
    pod_name = config.pod_name(run_id, role)
    pod_ids = tuple(sorted(pod.pod_id for pod in api.list_pods() if pod.name == pod_name))
    volume_ids = tuple(
        sorted(
            volume.volume_id
            for volume in api.list_network_volumes()
            if volume.name == pod_name or volume.name.startswith(f"{pod_name}-")
        )
    )
    evidence = CleanupEvidence(
        run_id=run_id,
        role=config.validate_role(role),
        pod_name=pod_name,
        matching_pod_ids=pod_ids,
        matching_volume_ids=volume_ids,
        clean=not pod_ids and not volume_ids,
    )
    if not evidence.clean:
        raise ValueError(
            f"RunPod cleanup incomplete: pods={list(pod_ids)}, networkVolumes={list(volume_ids)}"
        )
    return evidence

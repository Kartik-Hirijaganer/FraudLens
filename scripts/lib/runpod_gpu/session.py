"""Summary: RunPod session persistence, contract checks, status, and SSH argv rendering.

Key classes:
- (none)

Key functions:
- state_path: locate the bounded gitignored state file for one run.
- write_session: atomically persist non-secret local state.
- load_session: read and identity-check non-secret local state.
- validate_pod_contract: fail closed on provider drift from the frozen Pod contract.
- matching_pods: find exact-name Pods for duplicate-safe lifecycle operations.
- pod_status: return a redacted live status for one identity-matched Pod.
- ssh_argv: build key-file-based full-SSH commands without secret content.
- scp_argv: build key-file-based secure-copy commands without secret content.

Notes:
- Private key files are referenced by path and are never opened by this module.
- A session is keyed by run AND endpoint role, so a two-endpoint cascade run keeps one independent
  state file, Pod name, and watchdog per role instead of overwriting a single session.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

from lib.runpod_gpu.api import RunpodApi, RunpodPod
from lib.runpod_gpu.config import RunpodGpuConfig
from lib.runpod_gpu.models import PodStatus, RunpodSession

_SSH_TRANSPORT_OPTIONS = (
    "-o",
    "ServerAliveInterval=10",
    "-o",
    "ServerAliveCountMax=6",
    "-o",
    "TCPKeepAlive=yes",
)


def state_path(
    config: RunpodGpuConfig, repo_root: Path, run_id: str, role: str | None = None
) -> Path:
    """Return the bounded gitignored state path for one validated run and endpoint role."""
    validated = config.validate_role(role)
    name = f"session-{validated}.json" if validated else "session.json"
    return repo_root / config.state_dir / config.validate_run_id(run_id) / name


def write_session(config: RunpodGpuConfig, repo_root: Path, state: RunpodSession) -> RunpodSession:
    """Atomically persist one non-secret RunPod session."""
    path = state_path(config, repo_root, state.run_id, state.role)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".tmp")
    staging.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(staging, path)
    return state


def load_session(
    config: RunpodGpuConfig, repo_root: Path, run_id: str, role: str | None = None
) -> RunpodSession:
    """Load and identity-check one local RunPod session for a run and endpoint role."""
    state = RunpodSession.model_validate_json(
        state_path(config, repo_root, run_id, role).read_text(encoding="utf-8")
    )
    if (
        state.run_id != run_id
        or state.role != config.validate_role(role)
        or state.pod_name != config.pod_name(run_id, role)
    ):
        raise ValueError("RunPod session identity does not match the requested run")
    return state


def validate_pod_contract(
    config: RunpodGpuConfig, pod: RunpodPod, *, pod_name: str, expected_rate: Decimal
) -> None:
    """Reject any provider result that drifts from the frozen request."""
    failures = []
    if pod.name != pod_name:
        failures.append("name")
    if pod.gpu is not None and (
        pod.gpu.gpu_id != config.pod.gpu_id or pod.gpu.count != config.pod.gpu_count
    ):
        failures.append("GPU")
    if pod.image is not None and pod.image != config.pod.image_reference:
        failures.append("image")
    if pod.interruptible is True or pod.locked is True:
        failures.append("lifecycle")
    if pod.cost_per_hour != expected_rate:
        failures.append("hourly rate")
    # An explicit `false` is still a contract breach. An ABSENT value is not: the provider
    # stopped reporting encryption entirely, and the corpus on this volume is public synthetic
    # data. What is observed gets recorded on the session so the report discloses it rather than
    # implying a guarantee nobody made.
    if pod.volume_encrypted is False or pod.volume_in_gb != config.pod.volume_gb:
        failures.append("encrypted volume")
    if pod.volume_mount_path != config.pod.volume_mount_path or set(pod.ports) != set(
        config.pod.ports
    ):
        failures.append("network/storage")
    if pod.machine and pod.machine.secure_cloud is False:
        failures.append("Secure Cloud")
    if failures:
        raise ValueError(f"RunPod Pod violates frozen contract: {', '.join(failures)}")


def matching_pods(api: RunpodApi, pod_name: str) -> tuple[RunpodPod, ...]:
    """Return all exact-name Pods to enforce duplicate-safe creation."""
    return tuple(pod for pod in api.list_pods() if pod.name == pod_name)


def pod_status(
    config: RunpodGpuConfig,
    api: RunpodApi,
    *,
    run_id: str,
    repo_root: Path,
    role: str | None = None,
) -> PodStatus:
    """Return current redacted Pod lifecycle and SSH facts."""
    state = load_session(config, repo_root, run_id, role)
    pod = api.get_pod(state.pod_id)
    validate_pod_contract(config, pod, pod_name=state.pod_name, expected_rate=state.hourly_rate_usd)
    return PodStatus(
        run_id=run_id,
        role=state.role,
        pod_id=pod.pod_id,
        pod_name=pod.name,
        desired_status=pod.desired_status,
        gpu_id=pod.gpu.gpu_id if pod.gpu is not None else config.pod.gpu_id,
        hourly_rate_usd=pod.cost_per_hour,
        data_center_id=pod.machine.data_center_id if pod.machine else None,
        public_ip=str(pod.public_ip) if pod.public_ip else None,
        ssh_port=pod.ssh_port,
        volume_encrypted=pod.volume_encrypted,
    )


def _private_key_path(config: RunpodGpuConfig) -> Path:
    raw_path = os.environ.get(config.ssh.private_key_path_env, "")
    if not raw_path.strip():
        raise ValueError(f"{config.ssh.private_key_path_env} is required")
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise ValueError("configured SSH private key path is not a file")
    return path


def ssh_argv(config: RunpodGpuConfig, status: PodStatus) -> tuple[str, ...]:
    """Build a full-SSH argv without reading or copying private key content."""
    if status.desired_status != "RUNNING" or not status.public_ip or not status.ssh_port:
        raise ValueError("RunPod Pod is not ready for full SSH")
    return (
        "ssh",
        "-C",
        "-i",
        str(_private_key_path(config)),
        "-p",
        str(status.ssh_port),
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={config.ssh.connect_timeout_seconds}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        *_SSH_TRANSPORT_OPTIONS,
        f"{config.ssh.user}@{status.public_ip}",
    )


def scp_argv(config: RunpodGpuConfig, status: PodStatus) -> tuple[str, ...]:
    """Build the SCP prefix matching the verified full-SSH endpoint."""
    if status.desired_status != "RUNNING" or not status.public_ip or not status.ssh_port:
        raise ValueError("RunPod Pod is not ready for full SSH")
    return (
        "scp",
        "-C",
        "-i",
        str(_private_key_path(config)),
        "-P",
        str(status.ssh_port),
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={config.ssh.connect_timeout_seconds}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        *_SSH_TRANSPORT_OPTIONS,
    )

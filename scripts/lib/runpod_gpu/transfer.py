"""Summary: Hash-bound source, case, secret, and result transfer over full SSH.

Key classes:
- (none)

Key functions:
- sync_session: copy a committed tree, validated cases, and vLLM token to encrypted storage.
- export_session: retrieve and validate benchmark results before Pod teardown.

Notes:
- The bearer token crosses SSH through stdin and never appears in argv, logs, or state.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from lib.runpod_gpu.api import RunpodApi
from lib.runpod_gpu.config import RunpodGpuConfig
from lib.runpod_gpu.models import RunpodSession
from lib.runpod_gpu.planning import git_commit
from lib.runpod_gpu.session import (
    load_session,
    pod_status,
    scp_argv,
    ssh_argv,
    write_session,
)
from lib.vllm_bench.config import VllmBenchConfig
from lib.vllm_bench.state import load_case_bundle, load_run


def _now() -> datetime:
    return datetime.now(UTC)


def _run_ssh(
    argv: tuple[str, ...], remote_command: str, *, input_bytes: bytes | None = None
) -> None:
    subprocess.run((*argv, remote_command), check=True, input=input_bytes)


def _remote_sync_command(config: RunpodGpuConfig, commit: str) -> str:
    state_root = shlex.quote(config.remote.state_root)
    output_root = shlex.quote(config.remote.local_output_link)
    source_link = shlex.quote(config.remote.source_link)
    source_target = shlex.quote(f"{config.remote.state_root}/source-{commit}")
    return " ".join(
        (
            "set -Eeuo pipefail;",
            f"install -d -m 0700 {state_root} {output_root};",
            f"if [ -e {source_link} ] && [ ! -L {source_link} ]; then exit 2; fi;",
            f"rm -rf -- {source_target};",
            f"mkdir -p {source_target};",
            f"tar -xf - -C {source_target};",
            f"ln -s {output_root} {source_target}/.local;",
            f"ln -sfn {source_target} {source_link}",
        )
    )


def sync_session(  # noqa: PLR0913 - explicit operator inputs are security boundaries.
    config: RunpodGpuConfig,
    vllm_config: VllmBenchConfig,
    api: RunpodApi,
    *,
    run_id: str,
    cases_path: Path,
    repo_root: Path,
    confirmed: bool,
) -> RunpodSession:
    """Sync committed source, validated cases, and the vLLM token over full SSH."""
    if not confirmed:
        raise PermissionError("RunPod sync requires explicit confirmation")
    state = load_session(config, repo_root, run_id)
    if git_commit(repo_root) != state.git_commit or config.config_sha256 != state.config_sha256:
        raise ValueError("local Git or RunPod configuration changed after Pod creation")
    if re.fullmatch(r"cases-[a-z0-9-]+\.json", cases_path.name) is None:
        raise ValueError("case bundle must use the canonical cases-<source>-<profile>.json name")
    artifact, cases_sha = load_case_bundle(cases_path)
    if artifact.config_sha256 != vllm_config.config_sha256:
        raise ValueError("case bundle does not match the frozen vLLM configuration")
    token = os.environ.get(vllm_config.server.api_key_env, "")
    if not token.strip():
        raise ValueError(f"{vllm_config.server.api_key_env} is required")
    status = pod_status(config, api, run_id=run_id, repo_root=repo_root)
    ssh = ssh_argv(config, status)
    archive = subprocess.run(
        ("git", "archive", "--format=tar", state.git_commit),
        cwd=repo_root,
        check=True,
        capture_output=True,
    ).stdout
    _run_ssh(ssh, _remote_sync_command(config, state.git_commit), input_bytes=archive)
    setup_command = " ".join(
        (
            "set -Eeuo pipefail;",
            f"cd {shlex.quote(config.remote.source_link)};",
            "uv sync --all-packages --group fulldata --frozen",
        )
    )
    _run_ssh(ssh, setup_command)
    secret_command = " ".join(
        (
            "set -Eeuo pipefail;",
            "umask 077;",
            f"mkdir -p {shlex.quote(config.remote.state_root)};",
            f"cat > {shlex.quote(config.remote.api_key_path)}",
        )
    )
    _run_ssh(ssh, secret_command, input_bytes=token.encode())
    remote_case_dir = f"{config.remote.source_link}/{vllm_config.paths.output_dir}"
    _run_ssh(ssh, f"mkdir -p {shlex.quote(remote_case_dir)}")
    destination = f"{config.ssh.user}@{status.public_ip}:{remote_case_dir}/{cases_path.name}"
    subprocess.run((*scp_argv(config, status), str(cases_path), destination), check=True)
    return write_session(
        config,
        repo_root,
        state.model_copy(
            update={
                "cases_sha256": cases_sha,
                "cases_filename": cases_path.name,
                "synced_at": _now(),
            }
        ),
    )


def export_session(
    config: RunpodGpuConfig,
    api: RunpodApi,
    *,
    run_id: str,
    repo_root: Path,
) -> RunpodSession:
    """Retrieve the bound run directory and validate its case lineage locally."""
    state = load_session(config, repo_root, run_id)
    if not state.cases_sha256:
        raise ValueError("RunPod session must be synced before artifact export")
    status = pod_status(config, api, run_id=run_id, repo_root=repo_root)
    local_run = repo_root / ".local" / "vllm-bench" / run_id
    if local_run.exists():
        raise ValueError("local run directory already exists; refusing an ambiguous overwrite")
    local_run.parent.mkdir(parents=True, exist_ok=True)
    remote_run = f"{config.remote.source_link}/.local/vllm-bench/{run_id}"
    source = f"{config.ssh.user}@{status.public_ip}:{remote_run}"
    subprocess.run((*scp_argv(config, status), "-r", source, str(local_run.parent)), check=True)
    manifest = load_run(local_run / "run.json")
    if manifest.run_id != run_id or manifest.cases_sha256 != state.cases_sha256:
        raise ValueError("exported run identity or case lineage does not match the session")
    return write_session(config, repo_root, state.model_copy(update={"exported_at": _now()}))

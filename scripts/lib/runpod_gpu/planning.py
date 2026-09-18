"""Summary: Immutable Git, live capacity, budget admission, and Pod request planning.

Key classes:
- (none)

Key functions:
- build_plan: bind live GPU availability to the committed allocation admission policy.
- read_public_key: validate the public half of the operator SSH identity.
- build_create_request: render the SSH-only Pod request and automatic-stop watchdog.

Notes:
- The request contains only public SSH material and non-secret cache settings.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from lib.experiments.budget import BudgetConfig, Projection, admit
from lib.runpod_gpu.api import GpuInventoryItem
from lib.runpod_gpu.config import RunpodGpuConfig
from lib.runpod_gpu.models import CreatePodRequest, RunpodPlan
from lib.study import git_commit

_SSH_PUBLIC_KEY = re.compile(
    r"^(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp\d+) [A-Za-z0-9+/=]+(?: .*)?$"
)


def _inventory_item(
    config: RunpodGpuConfig, inventory: Sequence[GpuInventoryItem]
) -> GpuInventoryItem:
    matches = [item for item in inventory if item.gpu_id == config.pod.gpu_id]
    if len(matches) != 1:
        raise ValueError("RunPod inventory must contain exactly one configured GPU entry")
    return matches[0]


def build_plan(  # noqa: PLR0913 - the endpoint role is an explicit identity input.
    config: RunpodGpuConfig,
    budget: BudgetConfig,
    inventory: Sequence[GpuInventoryItem],
    *,
    run_id: str,
    repo_root: Path,
    role: str | None = None,
) -> RunpodPlan:
    """Bind current capacity, immutable source, and worst-case allocation admission."""
    config.validate_run_id(run_id)
    quote = budget.rates[config.rate_key]
    if (
        quote.provider != "runpod"
        or quote.sku != config.pod.gpu_id
        or quote.region != "secure-cloud"
        or quote.purchase_option != "pay_as_you_go"
    ):
        raise ValueError("RunPod configuration and budget quote do not match")
    watchdog = budget.watchdog_hours[config.allocation]
    projected = watchdog * quote.hourly_rate_usd
    decision = admit(
        Projection(
            projected_hours=watchdog,
            projected_cost_usd=projected,
            measurement_count=1,
        ),
        budget.allocations[config.allocation],
        admission_margin=budget.admission_margin,
    )
    gpu = _inventory_item(config, inventory)
    return RunpodPlan(
        run_id=run_id,
        role=config.validate_role(role),
        pod_name=config.pod_name(run_id, role),
        config_sha256=config.config_sha256,
        git_commit=git_commit(repo_root),
        gpu_id=gpu.gpu_id,
        gpu_memory_gb=gpu.memory_in_gb,
        available=gpu.available,
        secure_cloud=gpu.secure_cloud,
        stock_status=gpu.stock_status,
        image_reference=config.pod.image_reference,
        hourly_rate_usd=quote.hourly_rate_usd,
        watchdog_hours=watchdog,
        projected_cost_usd=projected,
        cost_with_margin_usd=decision.cost_with_margin_usd,
        allocation_usd=decision.allocation_usd,
        admitted=decision.admitted,
    )


def _bootstrap_command(watchdog_seconds: int, uv_version: str) -> str:
    """Return the fixed SSH bootstrap plus in-Pod automatic-stop watchdog."""
    return " ".join(
        (
            "set -Eeuo pipefail;",
            "apt-get update;",
            "DEBIAN_FRONTEND=noninteractive apt-get install --yes openssh-server;",
            f"python3 -m pip install --no-cache-dir uv=={uv_version};",
            "install -d -m 0700 /root/.ssh;",
            "install -d -m 0755 /run/sshd;",
            'printf "%s\\n" "$PUBLIC_KEY" > /root/.ssh/authorized_keys;',
            "chmod 0600 /root/.ssh/authorized_keys;",
            "printf 'PasswordAuthentication no\\nPermitRootLogin prohibit-password\\n'",
            "> /etc/ssh/sshd_config.d/fraudlens.conf;",
            "/usr/sbin/sshd;",
            f'( sleep {watchdog_seconds}; runpodctl pod stop "$RUNPOD_POD_ID" )',
            "> /workspace/.fraudlens-watchdog.log 2>&1 &",
            "exec sleep infinity",
        )
    )


def read_public_key(config: RunpodGpuConfig) -> str:
    """Read and validate the public half of the operator SSH identity."""
    raw_path = os.environ.get(config.ssh.public_key_path_env, "")
    if not raw_path.strip():
        raise ValueError(f"{config.ssh.public_key_path_env} is required")
    value = Path(raw_path).expanduser().read_text(encoding="utf-8").strip()
    if "\n" in value or _SSH_PUBLIC_KEY.fullmatch(value) is None:
        raise ValueError("SSH public key file must contain exactly one supported public key")
    return value


def build_create_request(
    config: RunpodGpuConfig, plan: RunpodPlan, *, public_key: str
) -> CreatePodRequest:
    """Build an SSH-only Pod payload without control-plane or vLLM secrets."""
    if (
        plan.config_sha256 != config.config_sha256
        or plan.pod_name != config.pod_name(plan.run_id, plan.role)
        or plan.image_reference != config.pod.image_reference
    ):
        raise ValueError("RunPod plan identity does not match the frozen configuration")
    if not plan.admitted or not plan.available or not plan.secure_cloud:
        raise ValueError("RunPod plan must be admitted with current Secure Cloud capacity")
    if _SSH_PUBLIC_KEY.fullmatch(public_key) is None:
        raise ValueError("a valid SSH public key is required")
    pod = config.pod
    watchdog_seconds = int(plan.watchdog_hours * Decimal(3600))
    return CreatePodRequest(
        name=plan.pod_name,
        cloudType=pod.cloud_type,
        computeType=pod.compute_type,
        gpuTypeIds=(pod.gpu_id,),
        gpuCount=pod.gpu_count,
        gpuTypePriority=pod.gpu_type_priority,
        imageName=pod.image_reference,
        interruptible=pod.interruptible,
        locked=pod.locked,
        containerDiskInGb=pod.container_disk_gb,
        volumeInGb=pod.volume_gb,
        volumeMountPath=pod.volume_mount_path,
        ports=pod.ports,
        globalNetworking=pod.global_networking,
        allowedCudaVersions=pod.allowed_cuda_versions,
        minRAMPerGPU=pod.min_ram_per_gpu_gb,
        minVCPUPerGPU=pod.min_vcpu_per_gpu,
        minDownloadMbps=pod.min_download_mbps,
        dockerEntrypoint=("bash", "-lc"),
        dockerStartCmd=(_bootstrap_command(watchdog_seconds, config.remote.uv_version),),
        env={
            "PUBLIC_KEY": public_key,
            "SSH_PUBLIC_KEY": public_key,
            "HF_HOME": f"{pod.volume_mount_path}/.cache/huggingface",
            "VLLM_CACHE_ROOT": f"{pod.volume_mount_path}/.cache/vllm",
        },
    )

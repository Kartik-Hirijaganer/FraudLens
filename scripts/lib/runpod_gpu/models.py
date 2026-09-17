"""Summary: Typed RunPod request, plan, session, status, and cleanup boundaries.

Key classes:
- CreatePodRequest: exact SSH-only RunPod REST payload.
- RunpodPlan: availability and worst-case budget admission before creation.
- RunpodSession: gitignored Pod identity and benchmark artifact lineage.
- PodStatus: redacted operator-facing lifecycle and SSH facts.
- CleanupEvidence: absence proof for matching Pods and network volumes.

Key functions:
- (none)

Notes:
- Models intentionally omit secret values and provider response environment data.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lib.runpod_gpu.config import IMAGE_DIGEST_PATTERN, RUN_ID_PATTERN
from lib.study import GIT_SHA_PATTERN

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class CreatePodRequest(BaseModel):
    """Exact API payload for one immutable, SSH-only Secure Cloud Pod.

    Volume encryption is NOT a field here: the provider rejects it on create
    ("key provided in request body which is not in input schema"). It remains a
    requirement — `validate_pod_contract` refuses any created Pod that does not report an
    encrypted volume — so the guarantee is verified on the result instead of requested in
    the call, and a provider that silently stopped encrypting would fail the contract.
    """

    model_config = _MODEL_CONFIG

    name: str = Field(..., min_length=1, description="Unique managed Pod name.")
    cloud_type: Literal["SECURE"] = Field(..., alias="cloudType", description="Cloud tier.")
    compute_type: Literal["GPU"] = Field(..., alias="computeType", description="Compute type.")
    gpu_type_ids: tuple[str, ...] = Field(
        ..., alias="gpuTypeIds", min_length=1, max_length=1, description="Exact GPU ID."
    )
    gpu_count: Literal[1] = Field(..., alias="gpuCount", description="GPU count.")
    gpu_type_priority: Literal["custom"] = Field(
        ..., alias="gpuTypePriority", description="Exact-ID scheduling priority."
    )
    image_name: str = Field(..., alias="imageName", min_length=1, description="Pinned image.")
    interruptible: Literal[False] = Field(..., description="On-demand Pod selection.")
    locked: Literal[False] = Field(..., description="Watchdog-compatible lifecycle lock.")
    container_disk_gb: int = Field(
        ..., alias="containerDiskInGb", ge=20, description="Container disk size."
    )
    volume_gb: int = Field(..., alias="volumeInGb", ge=20, description="Pod volume size.")
    volume_mount_path: str = Field(
        ..., alias="volumeMountPath", min_length=1, description="Pod volume mount."
    )
    ports: tuple[Literal["22/tcp"], ...] = Field(
        ..., min_length=1, max_length=1, description="SSH-only public ports."
    )
    global_networking: Literal[False] = Field(
        ..., alias="globalNetworking", description="Optional global network remains disabled."
    )
    allowed_cuda_versions: tuple[str, ...] = Field(
        ..., alias="allowedCudaVersions", min_length=1, description="Accepted CUDA versions."
    )
    min_ram_per_gpu: int = Field(
        ..., alias="minRAMPerGPU", ge=40, description="Minimum host RAM per GPU."
    )
    min_vcpu_per_gpu: int = Field(
        ..., alias="minVCPUPerGPU", ge=6, description="Minimum vCPUs per GPU."
    )
    min_download_mbps: int = Field(
        ..., alias="minDownloadMbps", gt=0, description="Minimum download bandwidth."
    )
    docker_entrypoint: tuple[str, ...] = Field(
        ..., alias="dockerEntrypoint", min_length=1, description="Image entrypoint override."
    )
    docker_start_command: tuple[str, ...] = Field(
        ..., alias="dockerStartCmd", min_length=1, description="Bootstrap command."
    )
    env: dict[str, str] = Field(..., min_length=1, description="Non-secret Pod environment.")


class RunpodPlan(BaseModel):
    """Live capacity and worst-case cost decision for one proposed Pod."""

    model_config = _MODEL_CONFIG

    run_id: str = Field(..., pattern=RUN_ID_PATTERN, description="Benchmark run ID.")
    pod_name: str = Field(..., min_length=1, description="Derived RunPod name.")
    role: str | None = Field(
        default=None, description="Endpoint role for a multi-endpoint run; None when single."
    )
    config_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="RunPod config hash.")
    git_commit: str = Field(..., pattern=GIT_SHA_PATTERN, description="Committed source revision.")
    gpu_id: str = Field(..., min_length=1, description="Requested GPU identifier.")
    gpu_memory_gb: int = Field(..., gt=0, description="Advertised GPU memory.")
    available: bool = Field(..., description="Current general GPU availability.")
    secure_cloud: bool = Field(..., description="Current Secure Cloud availability.")
    stock_status: str = Field(..., min_length=1, description="Provider stock assessment.")
    image_reference: str = Field(..., min_length=1, description="Immutable container image.")
    hourly_rate_usd: Decimal = Field(..., gt=0, description="Committed provider rate.")
    watchdog_hours: Decimal = Field(..., gt=0, description="Automatic stop deadline.")
    projected_cost_usd: Decimal = Field(..., ge=0, description="Worst-case compute cost.")
    cost_with_margin_usd: Decimal = Field(..., ge=0, description="Cost plus admission margin.")
    allocation_usd: Decimal = Field(..., gt=0, description="GPU benchmark allocation.")
    admitted: bool = Field(..., description="Whether the cost fits the allocation.")


class RunpodSession(BaseModel):
    """Non-secret local identity and lineage for one managed RunPod Pod."""

    model_config = _MODEL_CONFIG

    run_id: str = Field(..., pattern=RUN_ID_PATTERN, description="Benchmark run ID.")
    pod_id: str = Field(..., pattern=r"^[A-Za-z0-9-]+$", description="Provider Pod ID.")
    role: str | None = Field(
        default=None, description="Endpoint role for a multi-endpoint run; None when single."
    )
    pod_name: str = Field(..., pattern=r"^[a-z0-9-]+$", description="Managed Pod name.")
    config_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="RunPod config hash.")
    git_commit: str = Field(..., pattern=GIT_SHA_PATTERN, description="Synced Git revision.")
    image_digest: str = Field(
        ..., pattern=IMAGE_DIGEST_PATTERN, description="Pinned runtime image digest."
    )
    hourly_rate_usd: Decimal = Field(..., gt=0, description="Provider-reported hourly rate.")
    created_at: datetime = Field(..., description="UTC session creation time.")
    cases_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", description="Synced case bundle hash."
    )
    cases_filename: str | None = Field(
        default=None,
        pattern=r"^cases-[a-z0-9-]+\.json$",
        description="Synced case bundle filename.",
    )
    synced_at: datetime | None = Field(default=None, description="UTC source sync completion.")
    exported_at: datetime | None = Field(
        default=None, description="UTC artifact export completion."
    )
    deleted_at: datetime | None = Field(default=None, description="UTC Pod deletion request time.")


class PodStatus(BaseModel):
    """Redacted operator view of one managed Pod."""

    model_config = _MODEL_CONFIG

    run_id: str = Field(..., pattern=RUN_ID_PATTERN, description="Benchmark run ID.")
    pod_id: str = Field(..., min_length=1, description="Provider Pod ID.")
    role: str | None = Field(
        default=None, description="Endpoint role for a multi-endpoint run; None when single."
    )
    pod_name: str = Field(..., min_length=1, description="Managed Pod name.")
    desired_status: str = Field(..., min_length=1, description="Provider lifecycle state.")
    gpu_id: str = Field(..., min_length=1, description="Attached GPU identifier.")
    hourly_rate_usd: Decimal = Field(..., gt=0, description="Provider hourly rate.")
    data_center_id: str | None = Field(default=None, description="Placement data center.")
    public_ip: str | None = Field(default=None, description="Public SSH address when ready.")
    ssh_port: int | None = Field(default=None, ge=1, le=65535, description="Public SSH port.")
    volume_encrypted: bool | None = Field(
        default=None, description="Observed Pod volume encryption; None when unreported."
    )


class CleanupEvidence(BaseModel):
    """Read-only residue check for one managed Pod namespace."""

    model_config = _MODEL_CONFIG

    run_id: str = Field(..., pattern=RUN_ID_PATTERN, description="Benchmark run ID.")
    pod_name: str = Field(..., min_length=1, description="Checked resource name.")
    role: str | None = Field(
        default=None, description="Endpoint role for a multi-endpoint run; None when single."
    )
    matching_pod_ids: tuple[str, ...] = Field(..., description="Residual Pod IDs.")
    matching_volume_ids: tuple[str, ...] = Field(..., description="Residual network volume IDs.")
    clean: bool = Field(..., description="Whether all matching cloud resources are absent.")

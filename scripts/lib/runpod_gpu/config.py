"""Summary: Strict configuration for the ephemeral RunPod GPU benchmark host.

Key classes:
- PodConfig: immutable Secure Cloud GPU, image, storage, and network constraints.
- SshConfig: environment-backed SSH key paths and connection controls.
- RemoteConfig: bounded persistent paths inside the RunPod volume.
- RunpodGpuConfig: complete provider, budget, and local-state contract.

Key functions:
- load_config: parse YAML and bind its exact byte SHA-256.

Notes:
- Configuration contains environment-variable names only; secret values remain runtime-only.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_ROOT / "config" / "runpod-gpu.yaml"
RUN_ID_PATTERN = r"^vllm-bench-[0-9a-f]{16}$"
IMAGE_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_ENV_NAME_PATTERN = r"^[A-Z][A-Z0-9_]+$"
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class PodConfig(BaseModel):
    """Pinned RunPod Secure Cloud compute and storage request."""

    model_config = _MODEL_CONFIG

    cloud_type: Literal["SECURE"] = Field(..., description="Required RunPod cloud tier.")
    compute_type: Literal["GPU"] = Field(..., description="Required RunPod compute category.")
    gpu_id: Literal["NVIDIA GeForce RTX 4090"] = Field(
        ..., description="Exact pre-registered RunPod GPU identifier."
    )
    gpu_count: Literal[1] = Field(..., description="Single-GPU benchmark topology.")
    gpu_type_priority: Literal["custom"] = Field(
        ..., description="Preserve exact GPU selection rather than provider substitution."
    )
    image: str = Field(..., min_length=1, description="Container image repository.")
    image_digest: str = Field(
        ..., pattern=IMAGE_DIGEST_PATTERN, description="Pinned linux/amd64 image digest."
    )
    interruptible: Literal[False] = Field(..., description="Use on-demand rather than spot.")
    locked: Literal[False] = Field(..., description="Allow the watchdog to stop the Pod.")
    container_disk_gb: int = Field(..., ge=20, description="Ephemeral container disk size.")
    volume_gb: int = Field(..., ge=20, description="Encrypted Pod-local volume size.")
    volume_mount_path: str = Field(..., description="Absolute Pod volume mount path.")
    volume_encrypted: Literal[True] = Field(..., description="Require encrypted Pod storage.")
    ports: tuple[Literal["22/tcp"], ...] = Field(
        ..., min_length=1, max_length=1, description="Only the full-SSH port is public."
    )
    global_networking: Literal[False] = Field(
        ..., description="Disable optional RunPod global networking."
    )
    allowed_cuda_versions: tuple[Literal["12.8", "12.9", "13.0"], ...] = Field(
        ..., min_length=1, description="CUDA driver versions accepted by the pinned image."
    )
    min_ram_per_gpu_gb: int = Field(..., ge=40, description="Minimum host RAM per GPU.")
    min_vcpu_per_gpu: int = Field(..., ge=6, description="Minimum virtual CPUs per GPU.")
    min_download_mbps: int = Field(..., gt=0, description="Minimum model-download bandwidth.")

    @field_validator("volume_mount_path")
    @classmethod
    def _absolute_volume_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("volume_mount_path must be an absolute traversal-free path")
        return value

    @model_validator(mode="after")
    def _unique_cuda_versions(self) -> PodConfig:
        if len(set(self.allowed_cuda_versions)) != len(self.allowed_cuda_versions):
            raise ValueError("allowed_cuda_versions must be unique")
        return self

    @property
    def image_reference(self) -> str:
        """Return the immutable image reference sent to RunPod."""
        return f"{self.image}@{self.image_digest}"


class SshConfig(BaseModel):
    """SSH identity-path indirection and safe connection controls."""

    model_config = _MODEL_CONFIG

    user: Literal["root"] = Field(..., description="RunPod full-SSH user.")
    private_key_path_env: str = Field(
        ..., pattern=_ENV_NAME_PATTERN, description="Environment name holding private key path."
    )
    public_key_path_env: str = Field(
        ..., pattern=_ENV_NAME_PATTERN, description="Environment name holding public key path."
    )
    connect_timeout_seconds: int = Field(..., ge=5, le=120, description="SSH connection timeout.")


class RemoteConfig(BaseModel):
    """Bounded locations within the encrypted RunPod volume."""

    model_config = _MODEL_CONFIG

    source_link: str = Field(..., description="Stable symlink to the synced commit tree.")
    state_root: str = Field(..., description="Private operator state root on the Pod volume.")
    local_output_link: str = Field(..., description="Persistent target for repository .local.")
    api_key_path: str = Field(..., description="Mode-0600 vLLM bearer-token path.")
    uv_version: str = Field(
        ..., pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", description="Pinned remote uv version."
    )

    @field_validator("source_link", "state_root", "local_output_link", "api_key_path")
    @classmethod
    def _absolute_remote_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            not path.is_absolute()
            or ".." in path.parts
            or re.fullmatch(r"/[A-Za-z0-9._/-]+", value) is None
        ):
            raise ValueError("remote paths must be absolute, traversal-free, and shell-safe")
        return value

    @model_validator(mode="after")
    def _contained_paths(self) -> RemoteConfig:
        state = PurePosixPath(self.state_root)
        if not all(
            state == PurePosixPath(value) or state in PurePosixPath(value).parents
            for value in (self.local_output_link, self.api_key_path)
        ):
            raise ValueError("remote state paths must be contained by state_root")
        return self


class RunpodGpuConfig(BaseModel):
    """Complete RunPod provider, budget, state, SSH, and remote-path contract."""

    model_config = _MODEL_CONFIG

    api_base_url: HttpUrl = Field(..., description="RunPod REST API base URL.")
    api_key_env: str = Field(
        ..., pattern=_ENV_NAME_PATTERN, description="Environment name holding the API key."
    )
    name_prefix: str = Field(
        ..., pattern=r"^[a-z][a-z0-9-]+$", description="Prefix for all managed resources."
    )
    state_dir: str = Field(..., description="Repository-relative gitignored session state.")
    allocation: Literal["gpu_benchmark"] = Field(..., description="Budget allocation key.")
    rate_key: Literal["runpod_rtx4090_secure_payg"] = Field(
        ..., description="Budget rate quote key."
    )
    pod: PodConfig = Field(..., description="Pinned RunPod Pod request.")
    ssh: SshConfig = Field(..., description="SSH connection configuration.")
    remote: RemoteConfig = Field(..., description="Remote encrypted-volume paths.")
    config_sha256: str = Field(
        default="", exclude=True, pattern=r"^[0-9a-f]{64}$", description="Config byte hash."
    )

    @field_validator("state_dir")
    @classmethod
    def _local_state_only(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path.parts[:2] != (".local", "runpod-gpu"):
            raise ValueError("state_dir must stay at or below .local/runpod-gpu")
        return value

    def validate_run_id(self, run_id: str) -> str:
        """Reject names outside the deterministic benchmark run-id namespace."""
        if re.fullmatch(RUN_ID_PATTERN, run_id) is None:
            raise ValueError("run ID must match vllm-bench-<16 lowercase hex>")
        return run_id

    def pod_name(self, run_id: str) -> str:
        """Derive the sole managed Pod name for one validated run."""
        return f"{self.name_prefix}-{self.validate_run_id(run_id)}"


def load_config(path: Path = DEFAULT_CONFIG) -> RunpodGpuConfig:
    """Parse the RunPod YAML and bind its exact byte SHA-256."""
    raw = path.read_bytes()
    payload: Any = yaml.safe_load(raw)
    config = RunpodGpuConfig.model_validate(payload)
    return config.model_copy(update={"config_sha256": hashlib.sha256(raw).hexdigest()})

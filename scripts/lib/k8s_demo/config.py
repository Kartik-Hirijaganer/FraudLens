"""Summary: Typed configuration boundary for the local Kubernetes scaling and durability proof.

Key classes:
- K8sDemoConfigError: safe invalid-configuration failure.
- LoadConfig: validated request-load parameters shared by host orchestration and the in-cluster Job.
- K8sDemoConfig: immutable tool pins, cluster identity, timeouts, workload names, and load defaults.

Key functions:
- load_config: parse the committed non-secret YAML into the strict Pydantic contract.
- load_config_from_env: validate the in-cluster load Job environment.

Notes:
- The document contains no credentials. Kubernetes Secrets are supplied separately at runtime.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "k8s-demo.yaml"


class K8sDemoConfigError(RuntimeError):
    """Raised when the committed Kubernetes demo configuration is missing or invalid."""


class LoadConfig(BaseModel):
    """Validated settings for health-probe or durable-investigation load."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_url: HttpUrl = Field(..., description="HTTP base or endpoint targeted by the load Job.")
    mode: Literal["healthz", "investigations"] = Field(
        ..., description="Generate probe traffic or durable investigation work."
    )
    concurrency: int = Field(..., ge=1, le=256, description="Concurrent load workers.")
    duration_seconds: int = Field(..., ge=1, le=3600, description="Health-load duration.")
    reconnect_every: int = Field(
        ..., ge=1, description="Requests sent before replacing a persistent HTTP connection."
    )
    cases: int = Field(..., ge=1, le=500, description="Synthetic investigation cases to submit.")


class K8sDemoConfig(BaseModel):
    """Immutable local-demo and evidence policy loaded from config/k8s-demo.yaml."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cluster_name: str = Field(..., min_length=1, description="kind cluster name.")
    kubernetes_version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$", description="Schema pin.")
    kubectl_version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$", description="kubectl CLI pin.")
    kind_version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$", description="kind CLI pin.")
    kind_node_image: str = Field(
        ..., pattern=r"^kindest/node:v[^@]+@sha256:[0-9a-f]{64}$", description="Pinned node image."
    )
    kubeconform_version: str = Field(
        ..., pattern=r"^\d+\.\d+\.\d+$", description="Manifest validator CLI pin."
    )
    metrics_server_version: str = Field(
        ..., pattern=r"^\d+\.\d+\.\d+$", description="Pinned metrics-server release."
    )
    namespace: str = Field(..., min_length=1, description="Namespace containing the demo.")
    image: str = Field(..., min_length=1, description="Locally built backend image reference.")
    service: str = Field(..., min_length=1, description="API Service name.")
    smoke_base_url: HttpUrl = Field(..., description="Loopback base used by port-forward smoke.")
    local_port: int = Field(..., ge=1024, le=65535, description="Host smoke-test port.")
    startup_timeout_seconds: int = Field(..., ge=90, description="Workload rollout timeout.")
    metrics_timeout_seconds: int = Field(..., ge=60, description="Metrics API readiness timeout.")
    sample_interval_seconds: int = Field(..., ge=5, description="HPA evidence sample interval.")
    scale_up_timeout_seconds: int = Field(..., ge=60, description="Maximum scale-up wait.")
    scale_down_timeout_seconds: int = Field(..., ge=120, description="Maximum scale-down wait.")
    worker_claim_timeout_seconds: int = Field(
        ..., ge=5, le=300, description="Maximum wait for an active lease before worker deletion."
    )
    worker_claim_poll_seconds: float = Field(
        ..., ge=0.05, le=1, description="Active-lease observation interval."
    )
    worker_fault_lock_seconds: int = Field(
        ..., ge=60, le=300, description="Safety timeout for the disposable database barrier."
    )
    durability_timeout_seconds: int = Field(
        ..., ge=300, description="Maximum wait for every durable run to become terminal."
    )
    postgres_user: str = Field(..., min_length=1, description="Local proof database role.")
    postgres_database: str = Field(..., min_length=1, description="Local proof database name.")
    load: LoadConfig = Field(..., description="Default in-cluster load parameters.")

    @property
    def context(self) -> str:
        """Return the only kubectl context local mutations may target."""
        return f"kind-{self.cluster_name}"


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> K8sDemoConfig:
    """Load one strict, non-secret Kubernetes demo configuration document."""
    if not path.is_file():
        raise K8sDemoConfigError(f"Kubernetes demo config is missing: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        return K8sDemoConfig.model_validate(document)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise K8sDemoConfigError("Kubernetes demo config is invalid") from exc


def load_config_from_env() -> LoadConfig:
    """Validate the load Job's environment without accepting arbitrary extra variables."""
    values = {
        "target_url": os.environ.get("LOAD_TARGET_URL", ""),
        "mode": os.environ.get("LOAD_MODE", ""),
        "concurrency": os.environ.get("LOAD_CONCURRENCY", ""),
        "duration_seconds": os.environ.get("LOAD_DURATION_SECONDS", ""),
        "reconnect_every": os.environ.get("LOAD_RECONNECT_EVERY", ""),
        "cases": os.environ.get("LOAD_CASES", ""),
    }
    try:
        return LoadConfig.model_validate(values)
    except ValidationError as exc:
        raise K8sDemoConfigError("load Job environment is invalid") from exc

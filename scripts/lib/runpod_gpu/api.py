"""Summary: Typed, secret-safe RunPod REST client and GPU inventory reader.

Key classes:
- RunpodGpu: GPU identity attached to a Pod API response.
- RunpodMachine: scheduling facts attached to a Pod API response.
- RunpodPod: lifecycle, price, storage, and SSH facts for one Pod.
- NetworkVolume: one independently billed RunPod network volume.
- GpuInventoryItem: one `runpodctl gpu list` availability observation.
- RunpodApi: header-authenticated REST lifecycle client.

Key functions:
- api_key_from_env: load the RunPod API key from the configured process environment.
- read_gpu_inventory: query current RunPod GPU availability without mutating resources.

Notes:
- API keys are accepted only as headers or inherited process environment, never URLs or argv.
- HTTP failures intentionally omit response bodies so provider-returned values cannot leak.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, field_validator

from lib.runpod_gpu.config import RunpodGpuConfig
from lib.runpod_gpu.models import CreatePodRequest

_RESPONSE_CONFIG = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)


class RunpodGpu(BaseModel):
    """GPU identity returned for one RunPod Pod."""

    model_config = _RESPONSE_CONFIG

    gpu_id: str = Field(..., alias="id", min_length=1, description="RunPod GPU identifier.")
    count: int = Field(..., ge=1, description="Attached GPU count.")
    display_name: str | None = Field(
        default=None, alias="displayName", description="Human-readable GPU name."
    )


class RunpodMachine(BaseModel):
    """Non-sensitive placement facts for a running Pod."""

    model_config = _RESPONSE_CONFIG

    data_center_id: str | None = Field(
        default=None, alias="dataCenterId", description="RunPod data-center identifier."
    )
    secure_cloud: bool | None = Field(
        default=None, alias="secureCloud", description="Whether the placement is Secure Cloud."
    )


class RunpodPod(BaseModel):
    """RunPod Pod response reduced to lifecycle and governance evidence."""

    model_config = _RESPONSE_CONFIG

    pod_id: str = Field(
        ..., alias="id", pattern=r"^[A-Za-z0-9-]+$", description="Provider Pod identifier."
    )
    name: str = Field(..., min_length=1, description="Operator-assigned Pod name.")
    desired_status: Literal["RUNNING", "EXITED", "TERMINATED"] = Field(
        ..., alias="desiredStatus", description="Current expected lifecycle state."
    )
    image: str | None = Field(
        default=None,
        min_length=1,
        description="Container image reference when exposed by this response shape.",
    )
    interruptible: bool | None = Field(
        default=None,
        description="Whether the Pod uses interruptible pricing when provider-observable.",
    )
    locked: bool | None = Field(
        default=None,
        description="Whether lifecycle changes are disabled when provider-observable.",
    )
    cost_per_hour: Decimal = Field(
        ..., alias="costPerHr", gt=0, description="Provider-reported hourly compute rate."
    )
    gpu: RunpodGpu | None = Field(
        default=None, description="Attached GPU facts when exposed by this response shape."
    )
    machine: RunpodMachine | None = Field(default=None, description="Placement facts when ready.")
    public_ip: IPvAnyAddress | None = Field(
        default=None, alias="publicIp", description="Public SSH address when assigned."
    )

    @field_validator("public_ip", mode="before")
    @classmethod
    def _unassigned_ip_is_absent(cls, value: object) -> object:
        """Treat the provider's empty-string placeholder as "no address yet", not as an address.

        A freshly created Pod reports `publicIp: ""` until placement completes. Parsing that as
        an address fails the whole response, which previously left a BILLING Pod with no local
        session because the create call raised after the provider had already made it.
        """
        return None if isinstance(value, str) and not value.strip() else value

    port_mappings: dict[str, int] | None = Field(
        default=None, alias="portMappings", description="Container-to-public TCP port mappings."
    )
    ports: tuple[str, ...] = Field(default=(), description="Declared exposed Pod ports.")
    volume_encrypted: bool | None = Field(
        default=None,
        alias="volumeEncrypted",
        description="Reported Pod-local volume encryption; None when the provider omits it.",
    )
    volume_in_gb: int = Field(..., alias="volumeInGb", ge=0, description="Pod volume size.")
    volume_mount_path: str = Field(
        ..., alias="volumeMountPath", min_length=1, description="Pod volume mount path."
    )

    @property
    def ssh_port(self) -> int | None:
        """Return the public port mapped to container SSH port 22."""
        mappings = self.port_mappings or {}
        return mappings.get("22") or mappings.get("22/tcp")


class NetworkVolume(BaseModel):
    """An independently billed network volume returned by RunPod."""

    model_config = _RESPONSE_CONFIG

    volume_id: str = Field(..., alias="id", min_length=1, description="Network volume ID.")
    name: str = Field(..., min_length=1, description="Network volume name.")
    size_gb: int = Field(..., alias="size", gt=0, description="Allocated volume size in GB.")
    data_center_id: str = Field(
        ..., alias="dataCenterId", min_length=1, description="Volume data-center ID."
    )


class GpuInventoryItem(BaseModel):
    """One current GPU availability record from runpodctl."""

    model_config = _RESPONSE_CONFIG

    gpu_id: str = Field(..., alias="gpuId", min_length=1, description="RunPod GPU identifier.")
    display_name: str = Field(..., alias="displayName", min_length=1, description="GPU label.")
    available: bool = Field(..., description="Whether capacity is currently advertised.")
    secure_cloud: bool = Field(
        ..., alias="secureCloud", description="Whether Secure Cloud offers the GPU."
    )
    memory_in_gb: int = Field(..., alias="memoryInGb", gt=0, description="GPU memory in GB.")
    stock_status: str = Field(
        ..., alias="stockStatus", min_length=1, description="Provider stock assessment."
    )


class RunpodApi:
    """Small RunPod REST client that keeps authentication out of URLs and errors."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("RunPod API key is required")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> RunpodApi:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: Mapping[str, Any] | None = None,
        parse_json: bool = True,
    ) -> Any:
        response = self._client.request(method, path, json=json_payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            raise RuntimeError(
                f"RunPod API {method} {path} failed with status {response.status_code}"
            ) from None
        return response.json() if parse_json and response.content else None

    def list_pods(self) -> tuple[RunpodPod, ...]:
        """List all account Pods."""
        payload = self._request("GET", "/pods")
        return tuple(RunpodPod.model_validate(item) for item in payload)

    def get_pod(self, pod_id: str) -> RunpodPod:
        """Fetch one Pod by provider ID."""
        return RunpodPod.model_validate(self._request("GET", f"/pods/{pod_id}"))

    def create_pod(self, payload: CreatePodRequest) -> RunpodPod:
        """Create and deploy one Pod from a typed request."""
        body = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
        return RunpodPod.model_validate(self._request("POST", "/pods", json_payload=body))

    def start_pod(self, pod_id: str) -> None:
        """Start one stopped Pod."""
        self._request("POST", f"/pods/{pod_id}/start", parse_json=False)

    def stop_pod(self, pod_id: str) -> None:
        """Stop one running Pod while retaining its encrypted Pod volume."""
        self._request("POST", f"/pods/{pod_id}/stop", parse_json=False)

    def delete_pod(self, pod_id: str) -> None:
        """Permanently delete one stopped Pod and its Pod-local volume."""
        self._request("DELETE", f"/pods/{pod_id}", parse_json=False)

    def list_network_volumes(self) -> tuple[NetworkVolume, ...]:
        """List independently billed network volumes for residue checks."""
        payload = self._request("GET", "/networkvolumes")
        return tuple(NetworkVolume.model_validate(item) for item in payload)


def api_key_from_env(config: RunpodGpuConfig) -> str:
    """Read the required control-plane secret without printing or persisting it."""
    value = os.environ.get(config.api_key_env, "")
    if not value.strip():
        raise ValueError(f"{config.api_key_env} is required")
    return value


def read_gpu_inventory() -> tuple[GpuInventoryItem, ...]:
    """Read current GPU availability through runpodctl's JSON output."""
    result = subprocess.run(
        ("runpodctl", "gpu", "list"),
        check=True,
        capture_output=True,
        text=True,
    )
    payload: Any = json.loads(result.stdout)
    return tuple(GpuInventoryItem.model_validate(item) for item in payload)

"""Summary: Typed fake RunPod responses and sessions for provider-free operator tests.

Key classes:
- FakeApi: in-memory lifecycle client recording all requested mutations.

Key functions:
- pod: build one contract-matching RunPod Pod response.
- inventory: build the configured RTX 4090 availability record.
- session: build one non-secret local session identity.

Notes:
- All identifiers, addresses, and key material are synthetic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

from lib.runpod_gpu.api import GpuInventoryItem, NetworkVolume, RunpodApi, RunpodPod
from lib.runpod_gpu.config import RunpodGpuConfig
from lib.runpod_gpu.models import RunpodSession

RUN_ID = "vllm-bench-0123456789abcdef"
POD_ID = "synthetic-pod-id"
GIT_SHA = "a" * 40
PUBLIC_KEY = "ssh-ed25519 QUFBQQ== synthetic@example.invalid"


def pod(config: RunpodGpuConfig, **changes: object) -> RunpodPod:
    """Build one contract-matching synthetic Pod response."""
    payload: dict[str, object] = {
        "id": POD_ID,
        "name": config.pod_name(RUN_ID),
        "desiredStatus": "RUNNING",
        "image": config.pod.image_reference,
        "interruptible": False,
        "locked": False,
        "costPerHr": "0.740000",
        "gpu": {"id": config.pod.gpu_id, "count": 1, "displayName": "RTX 4090"},
        "machine": {"dataCenterId": "US-TEST-1", "secureCloud": True},
        "publicIp": "192.0.2.10",
        "portMappings": {"22": 22022},
        "ports": ["22/tcp"],
        "volumeEncrypted": True,
        "volumeInGb": config.pod.volume_gb,
        "volumeMountPath": config.pod.volume_mount_path,
    }
    payload.update(changes)
    return RunpodPod.model_validate(payload)


def inventory(config: RunpodGpuConfig, **changes: object) -> GpuInventoryItem:
    """Build one current synthetic GPU availability record."""
    payload: dict[str, object] = {
        "gpuId": config.pod.gpu_id,
        "displayName": "RTX 4090",
        "available": True,
        "secureCloud": True,
        "memoryInGb": 24,
        "stockStatus": "High",
    }
    payload.update(changes)
    return GpuInventoryItem.model_validate(payload)


def session(config: RunpodGpuConfig, **changes: object) -> RunpodSession:
    """Build one non-secret synthetic local session."""
    role = changes.get("role")
    payload: dict[str, object] = {
        "run_id": RUN_ID,
        "pod_id": POD_ID,
        "pod_name": config.pod_name(RUN_ID, role if isinstance(role, str) else None),
        "config_sha256": config.config_sha256,
        "git_commit": GIT_SHA,
        "image_digest": config.pod.image_digest,
        "hourly_rate_usd": Decimal("0.740000"),
        "created_at": datetime(2026, 9, 14, tzinfo=UTC),
    }
    payload.update(changes)
    return RunpodSession.model_validate(payload)


class FakeApi:
    """In-memory RunPod client with observable lifecycle calls."""

    def __init__(
        self,
        current: RunpodPod,
        *,
        pods: tuple[RunpodPod, ...] | None = None,
        volumes: tuple[NetworkVolume, ...] = (),
    ) -> None:
        self.current = current
        self.pods = pods if pods is not None else (current,)
        self.volumes = volumes
        self.calls: list[tuple[str, str]] = []

    def list_pods(self) -> tuple[RunpodPod, ...]:
        return self.pods

    def get_pod(self, pod_id: str) -> RunpodPod:
        self.calls.append(("get", pod_id))
        return self.current

    def create_pod(self, _payload: object) -> RunpodPod:
        self.calls.append(("create", self.current.pod_id))
        return self.current

    def start_pod(self, pod_id: str) -> None:
        self.calls.append(("start", pod_id))

    def stop_pod(self, pod_id: str) -> None:
        self.calls.append(("stop", pod_id))

    def delete_pod(self, pod_id: str) -> None:
        self.calls.append(("delete", pod_id))

    def list_network_volumes(self) -> tuple[NetworkVolume, ...]:
        return self.volumes


def typed_api(api: FakeApi) -> RunpodApi:
    """Narrow a fake to the structural API used by operator functions."""
    return cast(RunpodApi, api)

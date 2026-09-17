"""Summary: RunPod configuration, REST boundary, and live inventory tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- HTTP and runpodctl calls use synthetic transports and subprocess results only.
"""

from __future__ import annotations

import json
import subprocess

import httpx
import pytest
from pydantic import ValidationError
from runpod_gpu_fakes import RUN_ID, pod

from lib.runpod_gpu.api import RunpodApi, RunpodPod, api_key_from_env, read_gpu_inventory
from lib.runpod_gpu.config import RunpodGpuConfig, load_config
from lib.runpod_gpu.models import CreatePodRequest


def _payload() -> dict[str, object]:
    return load_config().model_dump(mode="json", exclude={"config_sha256"})


def test_config_pins_secure_single_gpu_and_bounded_paths() -> None:
    config = load_config()
    assert config.pod.cloud_type == "SECURE"
    assert config.pod.gpu_id == "NVIDIA GeForce RTX 4090"
    assert config.pod.gpu_count == 1
    assert config.pod.volume_encrypted is True
    assert config.pod.ports == ("22/tcp",)
    assert "@sha256:" in config.pod.image_reference
    assert config.pod_name(RUN_ID) == f"fraudlens-{RUN_ID}"
    with pytest.raises(ValueError, match="16 lowercase hex"):
        config.pod_name("wrong")


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda value: value["pod"].update({"cloud_type": "COMMUNITY"}), "SECURE"),
        (lambda value: value["pod"].update({"gpu_count": 2}), "Input should be 1"),
        (lambda value: value["pod"].update({"ports": ["8000/http"]}), "22/tcp"),
        (lambda value: value["pod"].update({"volume_encrypted": False}), "Input should be True"),
        (
            lambda value: value["pod"].update({"volume_mount_path": "../workspace"}),
            "absolute traversal-free",
        ),
        (
            lambda value: value.update({"state_dir": "docs/runpod"}),
            "below .local/runpod-gpu",
        ),
        (
            lambda value: value["remote"].update({"api_key_path": "/tmp/key"}),
            "contained by secret_root",
        ),
        (
            lambda value: value["remote"].update({"git_commit_path": "/tmp/commit"}),
            "contained by state_root",
        ),
        (
            lambda value: value["remote"].update(
                {
                    "secret_root": "/workspace/.fraudlens/secrets",
                    "api_key_path": "/workspace/.fraudlens/secrets/key",
                }
            ),
            "must not overlap",
        ),
    ),
)
def test_config_rejects_provider_and_path_drift(mutation, message: str) -> None:
    payload = _payload()
    mutation(payload)
    with pytest.raises(ValidationError, match=message):
        RunpodGpuConfig.model_validate(payload)


def test_rest_client_uses_header_auth_and_typed_lifecycle() -> None:
    config = load_config()
    current = pod(config)
    seen: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer synthetic-control-value"
        assert "synthetic-control-value" not in str(request.url)
        body = request.content.decode() if request.content else None
        seen.append((request.method, request.url.path, body))
        if request.url.path.endswith("/networkvolumes"):
            return httpx.Response(
                200,
                json=[{"id": "volume-id", "name": "volume", "size": 50, "dataCenterId": "X"}],
            )
        if request.method == "GET" and request.url.path.endswith("/pods"):
            return httpx.Response(200, json=[current.model_dump(mode="json", by_alias=True)])
        if request.method == "GET" or request.url.path.endswith("/pods"):
            return httpx.Response(200, json=current.model_dump(mode="json", by_alias=True))
        return httpx.Response(204)

    request = CreatePodRequest.model_validate(
        {
            "name": current.name,
            "cloudType": "SECURE",
            "computeType": "GPU",
            "gpuTypeIds": [config.pod.gpu_id],
            "gpuCount": 1,
            "gpuTypePriority": "custom",
            "imageName": config.pod.image_reference,
            "interruptible": False,
            "locked": False,
            "containerDiskInGb": 50,
            "volumeInGb": 50,
            "volumeMountPath": "/workspace",
            "ports": ["22/tcp"],
            "globalNetworking": False,
            "allowedCudaVersions": ["12.8"],
            "minRAMPerGPU": 40,
            "minVCPUPerGPU": 6,
            "minDownloadMbps": 100,
            "dockerEntrypoint": ["bash"],
            "dockerStartCmd": ["sleep infinity"],
            "env": {"PUBLIC_KEY": "public"},
        }
    )
    with RunpodApi(
        base_url=str(config.api_base_url),
        api_key="synthetic-control-value",
        transport=httpx.MockTransport(handler),
    ) as api:
        assert api.list_pods()[0].pod_id == current.pod_id
        assert api.get_pod(current.pod_id).ssh_port == 22022
        assert api.create_pod(request).name == current.name
        api.start_pod(current.pod_id)
        api.stop_pod(current.pod_id)
        api.delete_pod(current.pod_id)
        assert api.list_network_volumes()[0].size_gb == 50
    assert any(method == "POST" and body and "gpuTypeIds" in body for method, _, body in seen)
    assert not any(
        method == "POST" and body and "volumeEncrypted" in body for method, _, body in seen
    )


def test_rest_client_accepts_current_sparse_pod_response() -> None:
    """Lifecycle reads tolerate provider-omitted descriptive fields, but retain safety facts."""
    config = load_config()
    sparse = {
        "id": "synthetic-pod-id",
        "name": config.pod_name(RUN_ID),
        "desiredStatus": "EXITED",
        "costPerHr": "0.740000",
        "volumeEncrypted": True,
        "volumeInGb": config.pod.volume_gb,
        "volumeMountPath": config.pod.volume_mount_path,
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=sparse)

    with RunpodApi(
        base_url=str(config.api_base_url),
        api_key="synthetic-control-value",
        transport=httpx.MockTransport(handler),
    ) as api:
        observed = api.get_pod("synthetic-pod-id")

    assert observed.image is None
    assert observed.interruptible is None
    assert observed.locked is None
    assert observed.gpu is None
    assert observed.volume_encrypted is True


def test_rest_client_sanitizes_errors_and_rejects_blank_key() -> None:
    with pytest.raises(ValueError, match="required"):
        RunpodApi(base_url="https://example.invalid", api_key=" ")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="provider-secret-response")

    with (
        RunpodApi(
            base_url="https://example.invalid",
            api_key="synthetic",
            transport=httpx.MockTransport(handler),
        ) as api,
        pytest.raises(RuntimeError, match="status 403") as error,
    ):
        api.list_pods()
    assert "provider-secret-response" not in str(error.value)


def test_api_key_env_and_runpodctl_inventory(monkeypatch) -> None:
    config = load_config()
    monkeypatch.delenv(config.api_key_env, raising=False)
    with pytest.raises(ValueError, match="RUNPOD_API_KEY is required"):
        api_key_from_env(config)
    monkeypatch.setenv(config.api_key_env, "synthetic")
    assert api_key_from_env(config) == "synthetic"
    output = json.dumps(
        [
            {
                "gpuId": config.pod.gpu_id,
                "displayName": "RTX 4090",
                "available": True,
                "secureCloud": True,
                "memoryInGb": 24,
                "stockStatus": "High",
            }
        ]
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, stdout=output),
    )
    assert read_gpu_inventory()[0].secure_cloud is True


def test_a_freshly_created_pod_without_an_address_still_parses() -> None:
    """A Pod reports `publicIp: ""` until placement completes, and it is already billing.

    Parsing that as an address used to fail the whole create response, leaving a running Pod
    with no local session — the exact orphan the teardown evidence is supposed to prevent.
    """
    pod = RunpodPod.model_validate(
        {
            "id": "pod-1",
            "name": "fraudlens-vllm-bench-0123456789abcdef-awq",
            "desiredStatus": "RUNNING",
            "costPerHr": "0.740000",
            "publicIp": "",
            "volumeEncrypted": True,
            "volumeInGb": 50,
            "volumeMountPath": "/workspace",
        }
    )

    assert pod.public_ip is None
    assert pod.ssh_port is None
    assert pod.name.endswith("-awq")

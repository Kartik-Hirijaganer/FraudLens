"""Unit tests for the config-driven storage + job backend selectors."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest

import fraudlens_backend.backends.jobs as jobs_module
import fraudlens_backend.backends.storage as storage_module
from fraudlens_backend.backends import (
    AzureBlobStorage,
    ContainerAppsJobBackend,
    LocalFsStorage,
    LocalJobBackend,
    get_job_backend,
    get_storage_backend,
)
from fraudlens_backend.settings import AppSettings


def test_local_storage_round_trips(tmp_path: Path) -> None:
    settings = AppSettings(
        environment="dev", storage_backend="local", storage_local_dir=str(tmp_path)
    )
    backend = get_storage_backend(settings)
    assert isinstance(backend, LocalFsStorage)
    uri = backend.put("models/v1/model.bin", b"payload")
    assert uri.startswith("file://")
    assert backend.get("models/v1/model.bin") == b"payload"


def test_azure_storage_put_get_uses_blob_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_request(
        *,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout_seconds: float,
    ) -> tuple[int, bytes]:
        calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        return (201, b"") if method == "PUT" else (200, b"payload")

    monkeypatch.setattr(storage_module, "azure_http_request", fake_request)
    settings = AppSettings(
        environment="dev",
        storage_backend="azure_blob",
        azure_storage_account_name="acct",
        azure_storage_container_name="artifacts",
        azure_storage_sar_pdf_container_name="sar-pdfs",
        azure_storage_token_resource="https://storage.azure.com/",
    )
    backend = get_storage_backend(settings)
    assert isinstance(backend, AzureBlobStorage)
    monkeypatch.setattr(backend._tokens, "token", lambda resource: f"token-for-{resource}")

    uri = backend.put("sar/reports/r1.pdf", b"pdf")
    payload = backend.get("models/v1/model.bin")

    assert uri == "https://acct.blob.core.windows.net/sar-pdfs/sar/reports/r1.pdf"
    assert payload == b"payload"
    assert calls[0]["method"] == "PUT"
    assert calls[0]["url"] == uri
    assert calls[0]["body"] == b"pdf"
    assert calls[0]["headers"] == {
        "Authorization": "Bearer token-for-https://storage.azure.com/",
        "Content-Length": "3",
        "x-ms-blob-type": "BlockBlob",
        "x-ms-version": settings.azure_storage_blob_api_version,
    }
    assert calls[1]["method"] == "GET"
    assert calls[1]["url"] == "https://acct.blob.core.windows.net/artifacts/models/v1/model.bin"


def test_local_job_backend_returns_job_id() -> None:
    backend = get_job_backend(AppSettings(environment="dev", queue_backend="local"))
    assert isinstance(backend, LocalJobBackend)
    job_id = backend.submit("train_model", {"version": "v1"})
    assert isinstance(job_id, str) and len(job_id) == 32


def test_local_job_backend_can_execute_retrain_command(monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[dict[str, object]] = []

    def fake_run(command: list[str], *, check: bool) -> SimpleNamespace:
        runs.append({"command": command, "check": check})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(jobs_module.subprocess, "run", fake_run)
    backend = get_job_backend(
        AppSettings(
            environment="dev",
            queue_backend="local",
            local_job_execute_on_submit=True,
            local_retrain_command=["python", "scripts/retrain.py"],
        )
    )
    assert isinstance(backend, LocalJobBackend)
    job_id = backend.submit("retrain", {"trigger": "manual"})

    assert isinstance(job_id, str) and len(job_id) == 32
    assert runs == [{"command": ["python", "scripts/retrain.py"], "check": False}]


def test_container_apps_job_backend_starts_configured_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_request(
        *,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout_seconds: float,
    ) -> tuple[int, bytes]:
        calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        return 202, b'{"name":"exec-1"}'

    monkeypatch.setattr(jobs_module, "azure_http_request", fake_request)
    settings = AppSettings(
        environment="dev",
        queue_backend="container_apps_jobs",
        azure_managed_identity_token_url="http://metadata/token",
        azure_arm_endpoint="https://management.azure.com",
        azure_arm_token_resource="https://management.azure.com/",
        azure_subscription_id="sub",
        azure_resource_group_name="rg",
        azure_container_apps_retrain_job_name="fl-retrain",
    )
    backend = get_job_backend(settings)
    assert isinstance(backend, ContainerAppsJobBackend)
    monkeypatch.setattr(backend._tokens, "token", lambda resource: f"token-for-{resource}")

    job_id = backend.submit("retrain", {"trigger": "manual"})

    assert job_id == "exec-1"
    assert calls == [
        {
            "method": "POST",
            "url": "https://management.azure.com/subscriptions/sub/resourceGroups/rg/providers/"
            "Microsoft.App/jobs/fl-retrain/start?api-version=2024-03-01",
            "headers": {
                "Authorization": "Bearer token-for-https://management.azure.com/",
                "Content-Type": "application/json",
            },
            "body": b"{}",
            "timeout_seconds": settings.azure_rest_timeout_seconds,
        }
    ]

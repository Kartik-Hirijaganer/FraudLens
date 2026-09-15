"""Local and Azure Container Apps job backend tests."""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace

import pytest

import fraudlens_backend.backends.jobs as jobs_module
from fraudlens_backend.backends import (
    BackendConfigurationError,
    ContainerAppsJobBackend,
    LocalJobBackend,
    get_job_backend,
)
from fraudlens_backend.settings import AppSettings


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


def test_local_job_backend_rejects_unconfigured_execute_command() -> None:
    backend = get_job_backend(
        AppSettings(environment="dev", queue_backend="local", local_job_execute_on_submit=True)
    )

    with pytest.raises(BackendConfigurationError, match="no local command configured"):
        backend.submit("batch_score", {"trigger": "manual"})


def test_local_job_backend_wraps_start_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_to_start(command: list[str], *, check: bool) -> SimpleNamespace:
        del command, check
        raise OSError("missing executable")

    monkeypatch.setattr(jobs_module.subprocess, "run", fail_to_start)
    backend = get_job_backend(
        AppSettings(
            environment="dev",
            queue_backend="local",
            local_job_execute_on_submit=True,
            local_retrain_command=["missing"],
        )
    )

    with pytest.raises(RuntimeError, match="could not start"):
        backend.submit("retrain", {"trigger": "manual"})


def test_local_job_backend_raises_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        jobs_module.subprocess,
        "run",
        lambda _command, *, check: SimpleNamespace(returncode=1),
    )
    backend = get_job_backend(
        AppSettings(
            environment="dev",
            queue_backend="local",
            local_job_execute_on_submit=True,
            local_retrain_command=["false"],
        )
    )

    with pytest.raises(RuntimeError, match="local job retrain failed"):
        backend.submit("retrain", {"trigger": "manual"})


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


def test_container_apps_job_backend_starts_batch_score_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_request(**kwargs: object) -> tuple[int, bytes]:
        calls.append(dict(kwargs))
        return 200, b'{"id":"exec-id"}'

    monkeypatch.setattr(jobs_module, "azure_http_request", fake_request)
    settings = AppSettings(
        environment="dev",
        queue_backend="container_apps_jobs",
        azure_arm_endpoint="https://management.azure.com",
        azure_arm_token_resource="https://management.azure.com/",
        azure_subscription_id="sub",
        azure_resource_group_name="rg",
        azure_container_apps_batch_score_job_name="fl-batch",
    )
    backend = ContainerAppsJobBackend(settings)
    monkeypatch.setattr(backend._tokens, "token", lambda resource: f"token-for-{resource}")

    assert backend.submit("batch_score", {"trigger": "manual"}) == "exec-id"
    assert calls[0]["url"] == (
        "https://management.azure.com/subscriptions/sub/resourceGroups/rg/providers/"
        "Microsoft.App/jobs/fl-batch/start?api-version=2024-03-01"
    )


def test_container_apps_job_backend_requires_job_configuration() -> None:
    backend = ContainerAppsJobBackend(
        AppSettings(environment="dev", queue_backend="container_apps_jobs")
    )

    with pytest.raises(BackendConfigurationError, match="no Azure Container Apps Job"):
        backend.submit("retrain", {"trigger": "manual"})


def test_container_apps_job_backend_requires_arm_settings() -> None:
    backend = ContainerAppsJobBackend(
        AppSettings(
            environment="dev",
            queue_backend="container_apps_jobs",
            azure_container_apps_retrain_job_name="fl-retrain",
        )
    )

    with pytest.raises(BackendConfigurationError, match="settings are incomplete"):
        backend.submit("retrain", {"trigger": "manual"})


def test_container_apps_job_backend_reports_unexpected_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs_module, "azure_http_request", lambda **_kwargs: (500, b""))
    settings = AppSettings(
        environment="dev",
        queue_backend="container_apps_jobs",
        azure_arm_endpoint="https://management.azure.com",
        azure_arm_token_resource="https://management.azure.com/",
        azure_subscription_id="sub",
        azure_resource_group_name="rg",
        azure_container_apps_retrain_job_name="fl-retrain",
    )
    backend = ContainerAppsJobBackend(settings)
    monkeypatch.setattr(backend._tokens, "token", lambda resource: f"token-for-{resource}")

    with pytest.raises(RuntimeError, match="unexpected status 500"):
        backend.submit("retrain", {"trigger": "manual"})


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"", "fallback"),
        (b"not-json", "fallback"),
        (b'{"id":"exec-id"}', "exec-id"),
        (b"[]", "fallback"),
    ],
)
def test_job_execution_id_fallbacks(body: bytes, expected: str) -> None:
    assert jobs_module._job_execution_id(body, fallback="fallback") == expected

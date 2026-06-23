"""Summary: Config-driven background-job submission. The active backend is selected from
settings.queue_backend so investigation/batch/training work can be dispatched the same way locally
(local commands, no queue/no cloud) and on Azure Container Apps Jobs (scale-to-zero) in production.
The Azure backend starts configured Container Apps Jobs through ARM using managed identity.

Key classes:
- JobBackend: structural protocol for submitting a typed job, returning its id.
- LocalJobBackend: local command dispatch for the one-command demo.
- ContainerAppsJobBackend: Azure Container Apps Jobs ARM starter.

Key functions:
- get_job_backend: resolve the configured JobBackend from settings.

Notes:
- Job payloads carry no PHI (transaction ids/model versions only); the job-submitted
  log line is emitted through the redaction pipeline as defense-in-depth.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from typing import Protocol
from urllib.parse import quote
from uuid import uuid4

from fraudlens_backend.backends.azure import (
    BackendConfigurationError,
    ManagedIdentityTokenProvider,
    azure_http_request,
    configured_url,
)
from fraudlens_backend.middleware.logging import APP_LOGGER_NAME, get_logger
from fraudlens_backend.settings import AppSettings

_JOB_RETRAIN = "retrain"
_JOB_BATCH_SCORE = "batch_score"
_ARM_START_OK = {200, 202, 204}


class JobBackend(Protocol):
    """Submit a typed background job and return its job id; backend chosen by config."""

    def submit(self, job_type: str, payload: Mapping[str, object] | None = None) -> str:
        """Enqueue/dispatch a job of job_type and return its job id."""
        ...


class LocalJobBackend:
    """Local job dispatch for dev/UAT; can execute known commands synchronously."""

    def __init__(self, settings: AppSettings) -> None:
        """Store the local command settings."""
        self._settings = settings

    def submit(self, job_type: str, payload: Mapping[str, object] | None = None) -> str:
        """Record the submission, optionally execute the local command, and return a job id."""
        job_id = uuid4().hex
        get_logger(APP_LOGGER_NAME).info(
            "job.submitted", job_type=job_type, job_id=job_id, backend="local"
        )
        if self._settings.local_job_execute_on_submit:
            self._execute(job_type)
        return job_id

    def _execute(self, job_type: str) -> None:
        """Execute a known local job command for browser UAT."""
        command = self._command_for(job_type)
        if command is None:
            raise BackendConfigurationError(f"no local command configured for job type {job_type}")
        try:
            completed = subprocess.run(command, check=False)
        except OSError as exc:
            raise RuntimeError(f"local job {job_type} could not start") from exc
        if completed.returncode != 0:
            raise RuntimeError(f"local job {job_type} failed")

    def _command_for(self, job_type: str) -> list[str] | None:
        """Return the configured command for a supported local job type."""
        if job_type == _JOB_RETRAIN:
            return self._settings.local_retrain_command
        return None


class ContainerAppsJobBackend:
    """Azure Container Apps Jobs backend using ARM + managed identity."""

    def __init__(self, settings: AppSettings) -> None:
        """Store config and create the managed-identity token provider."""
        self._settings = settings
        self._tokens = ManagedIdentityTokenProvider(settings)

    def submit(self, job_type: str, payload: Mapping[str, object] | None = None) -> str:
        """Start the configured Container Apps Job and return the Azure job execution id/name."""
        job_name = self._job_name(job_type)
        url = self._start_url(job_name)
        del payload
        body = b"{}"
        status, response_body = azure_http_request(
            method="POST",
            url=url,
            headers={
                "Authorization": self._authorization_header(),
                "Content-Type": "application/json",
            },
            body=body,
            timeout_seconds=self._settings.azure_rest_timeout_seconds,
        )
        if status not in _ARM_START_OK:
            raise RuntimeError(f"Container Apps Job start returned unexpected status {status}")
        return _job_execution_id(response_body, fallback=job_name)

    def _job_name(self, job_type: str) -> str:
        """Return the configured Azure job name for a stable job type."""
        if job_type == _JOB_RETRAIN and self._settings.azure_container_apps_retrain_job_name:
            return self._settings.azure_container_apps_retrain_job_name
        if (
            job_type == _JOB_BATCH_SCORE
            and self._settings.azure_container_apps_batch_score_job_name
        ):
            return self._settings.azure_container_apps_batch_score_job_name
        raise BackendConfigurationError(f"no Azure Container Apps Job configured for {job_type}")

    def _authorization_header(self) -> str:
        """Return the ARM bearer header value."""
        token = self._tokens.token(self._settings.azure_arm_token_resource)
        return f"Bearer {token}"

    def _start_url(self, job_name: str) -> str:
        """Build the ARM start URL from configured non-secret resource names."""
        arm_endpoint = self._settings.azure_arm_endpoint
        subscription_id = self._settings.azure_subscription_id
        resource_group = self._settings.azure_resource_group_name
        if not arm_endpoint or not subscription_id or not resource_group:
            raise BackendConfigurationError("Azure Container Apps Job settings are incomplete")
        escaped_group = quote(resource_group, safe="")
        escaped_job = quote(job_name, safe="")
        path = (
            f"subscriptions/{subscription_id}/resourceGroups/{escaped_group}/providers/"
            f"Microsoft.App/jobs/{escaped_job}/start"
            f"?api-version={self._settings.azure_container_apps_api_version}"
        )
        return configured_url(arm_endpoint, path)


def _job_execution_id(response_body: bytes, *, fallback: str) -> str:
    """Extract a stable execution id/name from ARM's response when present."""
    if not response_body:
        return fallback
    try:
        payload = json.loads(response_body.decode("utf-8"))
    except json.JSONDecodeError:
        return fallback
    if isinstance(payload, dict):
        for key in ("name", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return fallback


def get_job_backend(settings: AppSettings) -> JobBackend:
    """Return the JobBackend selected by settings.queue_backend."""
    if settings.queue_backend == "local":
        return LocalJobBackend(settings)
    return ContainerAppsJobBackend(settings)

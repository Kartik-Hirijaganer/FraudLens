"""Summary: Config-driven background-job submission. The active backend is selected
from settings.queue_backend so investigation/batch/training work can be dispatched the
same way locally (an in-process runner — no queue, no worker, no cloud) and on Azure
Container Apps Jobs (scale-to-zero) in production. Phase 1 ships the local runner
skeleton (records + returns a job id); the real job_executions persistence and the
Container Apps Jobs trigger land in Phase 2 and Phase 14 respectively.

Key classes:
- JobBackend: structural protocol for submitting a typed job, returning its id.
- LocalJobBackend: in-process job dispatch for the one-command demo.
- ContainerAppsJobBackend: placeholder for the Azure Jobs backend (lands in Phase 14).

Key functions:
- get_job_backend: resolve the configured JobBackend from settings.

Notes:
- Job payloads carry no PHI (transaction ids/model versions only); the job-submitted
  log line is emitted through the redaction pipeline as defense-in-depth.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from uuid import uuid4

from fraudlens_backend.middleware.logging import APP_LOGGER_NAME, get_logger
from fraudlens_backend.settings import AppSettings

_AZURE_DEFERRED = "Container Apps Jobs submission lands in Phase 14 (scale-to-zero)."


class JobBackend(Protocol):
    """Submit a typed background job and return its job id; backend chosen by config."""

    def submit(self, job_type: str, payload: Mapping[str, object] | None = None) -> str:
        """Enqueue/dispatch a job of job_type and return its job id."""
        ...


class LocalJobBackend:
    """In-process job dispatch for local dev; returns a generated job id."""

    def submit(self, job_type: str, payload: Mapping[str, object] | None = None) -> str:
        """Record the submission and return a fresh job id (no external queue)."""
        job_id = uuid4().hex
        get_logger(APP_LOGGER_NAME).info(
            "job.submitted", job_type=job_type, job_id=job_id, backend="local"
        )
        return job_id


class ContainerAppsJobBackend:
    """Placeholder Azure Container Apps Jobs backend; operational in Phase 14."""

    def submit(self, job_type: str, payload: Mapping[str, object] | None = None) -> str:
        """Not yet implemented — Container Apps Jobs dispatch lands in Phase 14."""
        raise NotImplementedError(_AZURE_DEFERRED)


def get_job_backend(settings: AppSettings) -> JobBackend:
    """Return the JobBackend selected by settings.queue_backend."""
    if settings.queue_backend == "local":
        return LocalJobBackend()
    return ContainerAppsJobBackend()

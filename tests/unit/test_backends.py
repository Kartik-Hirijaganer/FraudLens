"""Unit tests for the config-driven storage + job backend selectors."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_azure_storage_is_selected_but_deferred() -> None:
    backend = get_storage_backend(AppSettings(environment="dev", storage_backend="azure_blob"))
    assert isinstance(backend, AzureBlobStorage)
    with pytest.raises(NotImplementedError):
        backend.put("k", b"")
    with pytest.raises(NotImplementedError):
        backend.get("k")


def test_local_job_backend_returns_job_id() -> None:
    backend = get_job_backend(AppSettings(environment="dev", queue_backend="local"))
    assert isinstance(backend, LocalJobBackend)
    job_id = backend.submit("train_model", {"version": "v1"})
    assert isinstance(job_id, str) and len(job_id) == 32


def test_container_apps_job_backend_is_selected_but_deferred() -> None:
    backend = get_job_backend(AppSettings(environment="dev", queue_backend="container_apps_jobs"))
    assert isinstance(backend, ContainerAppsJobBackend)
    with pytest.raises(NotImplementedError):
        backend.submit("train_model")

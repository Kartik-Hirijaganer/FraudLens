"""Config-driven infrastructure backends (plan §12.3): storage + background jobs.

A `local` selection makes `make local-demo` run with no cloud and no keys; the cloud
selections (Azure Blob / Container Apps Jobs) are placeholders until Phase 14. Re-exports
are intentional (see members).
"""

from __future__ import annotations

from fraudlens_backend.backends.jobs import (
    ContainerAppsJobBackend,
    JobBackend,
    LocalJobBackend,
    get_job_backend,
)
from fraudlens_backend.backends.storage import (
    AzureBlobStorage,
    LocalFsStorage,
    StorageBackend,
    get_storage_backend,
)

__all__ = [
    "AzureBlobStorage",
    "ContainerAppsJobBackend",
    "JobBackend",
    "LocalFsStorage",
    "LocalJobBackend",
    "StorageBackend",
    "get_job_backend",
    "get_storage_backend",
]

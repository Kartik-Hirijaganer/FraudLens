"""Summary: Config-driven infrastructure backends (plan §12.3): storage + background jobs.

A `local` selection makes `make local-demo` run with no cloud and no keys; the cloud
selections use managed-identity Azure REST clients so production can upload SAR PDFs and start
Container Apps Jobs without long-lived credentials. Re-exports are intentional (see members).

Key classes:
- LocalFsStorage / AzureBlobStorage: artifact and SAR-PDF blob stores.
- LocalJobBackend / ContainerAppsJobBackend: job submission backends.
- BackendConfigurationError / BackendRequestError: PHI-free backend failure types.

Key functions:
- get_storage_backend: resolve the configured storage backend.
- get_job_backend: resolve the configured job backend.

Notes:
- Secrets still come from Infisical/runtime identity; only non-secret resource names are settings.
"""

from __future__ import annotations

from fraudlens_backend.backends.azure import BackendConfigurationError, BackendRequestError
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
    "BackendConfigurationError",
    "BackendRequestError",
    "ContainerAppsJobBackend",
    "JobBackend",
    "LocalFsStorage",
    "LocalJobBackend",
    "StorageBackend",
    "get_job_backend",
    "get_storage_backend",
]

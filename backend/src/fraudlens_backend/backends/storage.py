"""Summary: Config-driven artifact storage (model artifacts, SAR PDFs). The active
backend is selected from settings.storage_backend so the same code path serves the
one-command local demo (local filesystem, no cloud/keys) and Azure Blob in production
without an `if cloud:` scattered through callers. Phase 1 ships the local-FS backend
fully; the Azure Blob backend is a constructible placeholder whose operations raise
until Phase 14 wires the real SDK + managed-identity credentials.

Key classes:
- StorageBackend: structural protocol for a put/get blob store.
- LocalFsStorage: filesystem-backed store under a (gitignored) root directory.
- AzureBlobStorage: placeholder for the Azure Blob backend (lands in Phase 14).

Key functions:
- get_storage_backend: resolve the configured StorageBackend from settings.

Notes:
- put() returns a stable URI reference (file:// for local) recorded on the owning row;
  no PHI ever appears in keys/URIs (keys are content/model identifiers).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from fraudlens_backend.settings import AppSettings

_AZURE_DEFERRED = "Azure Blob storage lands in Phase 14 (managed-identity credentials)."


class StorageBackend(Protocol):
    """A minimal put/get blob store; concrete backends are selected by config."""

    def put(self, key: str, data: bytes) -> str:
        """Store data under key and return a stable URI reference."""
        ...

    def get(self, key: str) -> bytes:
        """Return the bytes previously stored under key."""
        ...


class LocalFsStorage:
    """Filesystem storage under a single root directory (the local-demo default)."""

    def __init__(self, root: Path) -> None:
        """Store the (absolute) root directory used for all keys."""
        self._root = root

    def put(self, key: str, data: bytes) -> str:
        """Write data to <root>/<key> (creating parents) and return its file:// URI."""
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path.as_uri()

    def get(self, key: str) -> bytes:
        """Read and return the bytes stored at <root>/<key>."""
        return (self._root / key).read_bytes()


class AzureBlobStorage:
    """Placeholder Azure Blob backend; constructible now, operational in Phase 14."""

    def put(self, key: str, data: bytes) -> str:
        """Not yet implemented — Azure Blob upload lands in Phase 14."""
        raise NotImplementedError(_AZURE_DEFERRED)

    def get(self, key: str) -> bytes:
        """Not yet implemented — Azure Blob download lands in Phase 14."""
        raise NotImplementedError(_AZURE_DEFERRED)


def get_storage_backend(settings: AppSettings) -> StorageBackend:
    """Return the StorageBackend selected by settings.storage_backend."""
    if settings.storage_backend == "local":
        return LocalFsStorage(Path(settings.storage_local_dir).resolve())
    return AzureBlobStorage()

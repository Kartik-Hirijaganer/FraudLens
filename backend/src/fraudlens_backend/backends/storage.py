"""Summary: Config-driven artifact storage (model artifacts, SAR PDFs). The active backend is
selected from settings.storage_backend so the same code path serves the one-command local demo
(local filesystem, no cloud/keys) and Azure Blob in production without cloud-specific branching in
callers. The Azure Blob backend uses managed-identity authenticated REST calls, keeping the image
thin while making the Phase 14 cloud selector operational.

Key classes:
- StorageBackend: structural protocol for a put/get blob store.
- LocalFsStorage: filesystem-backed store under a (gitignored) root directory.
- AzureBlobStorage: Azure Blob REST backend using the app's managed identity.

Key functions:
- get_storage_backend: resolve the configured StorageBackend from settings.

Notes:
- put() returns a stable URI reference (file:// for local) recorded on the owning row;
  no PHI ever appears in keys/URIs (keys are content/model identifiers).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from fraudlens_backend.backends.azure import (
    BackendConfigurationError,
    ManagedIdentityTokenProvider,
    azure_http_request,
    configured_url,
)
from fraudlens_backend.settings import AppSettings

_SAR_KEY_PREFIX = "sar/"
_BLOB_CREATED = 201
_BLOB_OK = 200


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
    """Azure Blob backend using managed identity + direct REST calls."""

    def __init__(self, settings: AppSettings) -> None:
        """Store config and create the managed-identity token provider."""
        self._settings = settings
        self._tokens = ManagedIdentityTokenProvider(settings)

    def put(self, key: str, data: bytes) -> str:
        """Upload a block blob and return its stable HTTPS URI."""
        url = self._blob_url(key)
        status, _body = azure_http_request(
            method="PUT",
            url=url,
            headers={
                "Authorization": self._authorization_header(),
                "Content-Length": str(len(data)),
                "x-ms-blob-type": "BlockBlob",
                "x-ms-version": self._settings.azure_storage_blob_api_version,
            },
            body=data,
            timeout_seconds=self._settings.azure_rest_timeout_seconds,
        )
        if status != _BLOB_CREATED:
            raise RuntimeError(f"Azure Blob upload returned unexpected status {status}")
        return url

    def get(self, key: str) -> bytes:
        """Download and return blob bytes."""
        status, body = azure_http_request(
            method="GET",
            url=self._blob_url(key),
            headers={
                "Authorization": self._authorization_header(),
                "x-ms-version": self._settings.azure_storage_blob_api_version,
            },
            timeout_seconds=self._settings.azure_rest_timeout_seconds,
        )
        if status != _BLOB_OK:
            raise RuntimeError(f"Azure Blob download returned unexpected status {status}")
        return body

    def _endpoint(self) -> str:
        """Return the configured Blob endpoint, deriving it from account name when needed."""
        if self._settings.azure_storage_blob_endpoint:
            return self._settings.azure_storage_blob_endpoint
        if not self._settings.azure_storage_account_name:
            raise BackendConfigurationError(
                "azure_storage_account_name is required for Azure Blob storage"
            )
        return (
            f"https://{self._settings.azure_storage_account_name}."
            f"{self._settings.azure_storage_blob_host_suffix}"
        )

    def _container_for(self, key: str) -> str:
        """Route SAR PDFs to their own lifecycle-managed container."""
        if key.startswith(_SAR_KEY_PREFIX):
            return self._settings.azure_storage_sar_pdf_container_name
        return self._settings.azure_storage_container_name

    def _blob_url(self, key: str) -> str:
        """Build the data-plane URL for one blob key."""
        normalized = key.strip("/")
        escaped_container = quote(self._container_for(normalized), safe="")
        escaped_key = quote(normalized, safe="/")
        return configured_url(self._endpoint(), f"{escaped_container}/{escaped_key}")

    def _authorization_header(self) -> str:
        """Return the Blob data-plane bearer header value."""
        token = self._tokens.token(self._settings.azure_storage_token_resource)
        return f"Bearer {token}"


def get_storage_backend(settings: AppSettings) -> StorageBackend:
    """Return the StorageBackend selected by settings.storage_backend."""
    if settings.storage_backend == "local":
        return LocalFsStorage(Path(settings.storage_local_dir).resolve())
    return AzureBlobStorage(settings)

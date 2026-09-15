"""Azure HTTP and local/Azure storage backend tests."""

from __future__ import annotations

import urllib.error
from collections.abc import Mapping
from pathlib import Path

import pytest
from http_fakes import (
    _HttpResponse,
)

import fraudlens_backend.backends.azure as azure_module
import fraudlens_backend.backends.storage as storage_module
from fraudlens_backend.backends import (
    AzureBlobStorage,
    BackendConfigurationError,
    BackendRequestError,
    LocalFsStorage,
    LocalJobBackend,
    get_job_backend,
    get_storage_backend,
)
from fraudlens_backend.settings import AppSettings


def test_azure_http_request_returns_status_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_urlopen(request: object, *, timeout: float) -> _HttpResponse:
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["body"] = request.data
        seen["timeout"] = timeout
        return _HttpResponse()

    monkeypatch.setattr(azure_module.urllib.request, "urlopen", fake_urlopen)

    status, body = azure_module.azure_http_request(
        method="POST",
        url="https://example.test/resource",
        headers={"X-Test": "1"},
        body=b"payload",
        timeout_seconds=2.5,
    )

    assert status == 204
    assert body == b"ok"
    assert seen == {
        "url": "https://example.test/resource",
        "method": "POST",
        "body": b"payload",
        "timeout": 2.5,
    }


def test_azure_http_request_wraps_http_and_url_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_with_http(_request: object, *, timeout: float) -> _HttpResponse:
        del timeout
        raise urllib.error.HTTPError("https://example.test", 500, "failed", None, None)

    monkeypatch.setattr(azure_module.urllib.request, "urlopen", fail_with_http)
    with pytest.raises(BackendRequestError, match="status 500"):
        azure_module.azure_http_request(
            method="GET", url="https://example.test", timeout_seconds=1.0
        )

    def fail_with_url(_request: object, *, timeout: float) -> _HttpResponse:
        del timeout
        raise urllib.error.URLError("unreachable")

    monkeypatch.setattr(azure_module.urllib.request, "urlopen", fail_with_url)
    with pytest.raises(BackendRequestError, match="before a response"):
        azure_module.azure_http_request(
            method="GET", url="https://example.test", timeout_seconds=1.0
        )


def test_managed_identity_token_provider_requests_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_request(**kwargs: object) -> tuple[int, bytes]:
        calls.append(dict(kwargs))
        return 200, b'{"access_token":"token-1","expires_on":"9999999999"}'

    monkeypatch.setattr(azure_module, "azure_http_request", fake_request)
    provider = azure_module.ManagedIdentityTokenProvider(
        AppSettings(
            environment="dev",
            azure_managed_identity_token_url="http://metadata/token?existing=1",
            azure_managed_identity_client_id="client-id",
            azure_rest_timeout_seconds=2.5,
        )
    )

    assert provider.token("https://resource.example/") == "token-1"
    assert provider.token("https://resource.example/") == "token-1"

    assert calls == [
        {
            "method": "GET",
            "url": "http://metadata/token?existing=1&api-version=2018-02-01"
            "&resource=https%3A%2F%2Fresource.example%2F&client_id=client-id",
            "headers": {"Metadata": "true"},
            "timeout_seconds": 2.5,
        }
    ]


def test_managed_identity_token_provider_requires_resource() -> None:
    provider = azure_module.ManagedIdentityTokenProvider(AppSettings(environment="dev"))

    with pytest.raises(BackendConfigurationError, match="azure token resource"):
        provider.token("")


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (503, b"{}", "returned status 503"),
        (200, b"not-json", "was not JSON"),
        (200, b'["token"]', "was not an object"),
        (200, b"{}", "did not include a token"),
    ],
)
def test_managed_identity_token_provider_rejects_bad_responses(
    monkeypatch: pytest.MonkeyPatch, status: int, body: bytes, message: str
) -> None:
    monkeypatch.setattr(
        azure_module,
        "azure_http_request",
        lambda **_kwargs: (status, body),
    )
    provider = azure_module.ManagedIdentityTokenProvider(
        AppSettings(environment="dev", azure_managed_identity_token_url="http://metadata/token")
    )

    with pytest.raises(BackendRequestError, match=message):
        provider.token("https://resource.example/")


def test_managed_identity_token_provider_defaults_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        azure_module,
        "azure_http_request",
        lambda **_kwargs: (200, b'{"access_token":"token-1"}'),
    )
    provider = azure_module.ManagedIdentityTokenProvider(
        AppSettings(environment="dev", azure_managed_identity_token_url="http://metadata/token")
    )

    assert provider.token("https://resource.example/") == "token-1"


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


def test_azure_storage_uses_configured_endpoint_and_reports_upload_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_request(**kwargs: object) -> tuple[int, bytes]:
        calls.append(dict(kwargs))
        return 500, b""

    monkeypatch.setattr(storage_module, "azure_http_request", fake_request)
    settings = AppSettings(
        environment="dev",
        storage_backend="azure_blob",
        azure_storage_blob_endpoint="https://custom.blob",
        azure_storage_token_resource="https://storage.azure.com/",
    )
    backend = AzureBlobStorage(settings)
    monkeypatch.setattr(backend._tokens, "token", lambda resource: f"token-for-{resource}")

    with pytest.raises(RuntimeError, match="upload returned unexpected status 500"):
        backend.put("models/v1/model.bin", b"payload")

    assert calls[0]["url"] == "https://custom.blob/artifacts/models/v1/model.bin"


def test_azure_storage_reports_download_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage_module, "azure_http_request", lambda **_kwargs: (404, b""))
    backend = AzureBlobStorage(
        AppSettings(
            environment="dev",
            storage_backend="azure_blob",
            azure_storage_account_name="acct",
            azure_storage_token_resource="https://storage.azure.com/",
        )
    )
    monkeypatch.setattr(backend._tokens, "token", lambda resource: f"token-for-{resource}")

    with pytest.raises(RuntimeError, match="download returned unexpected status 404"):
        backend.get("models/v1/model.bin")


def test_azure_storage_requires_account_without_endpoint() -> None:
    backend = AzureBlobStorage(
        AppSettings(
            environment="dev",
            storage_backend="azure_blob",
            azure_storage_token_resource="https://storage.azure.com/",
        )
    )

    with pytest.raises(BackendConfigurationError, match="azure_storage_account_name"):
        backend.put("models/v1/model.bin", b"payload")


def test_local_job_backend_returns_job_id() -> None:
    backend = get_job_backend(AppSettings(environment="dev", queue_backend="local"))
    assert isinstance(backend, LocalJobBackend)
    job_id = backend.submit("train_model", {"version": "v1"})
    assert isinstance(job_id, str) and len(job_id) == 32

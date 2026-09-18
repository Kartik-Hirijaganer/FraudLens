"""Summary: Minimal Azure REST helpers for managed-identity authenticated runtime calls.
The backend uses these helpers for the Phase 14 cloud selectors without adding Azure SDK weight to
the serving image: a managed identity token is requested from the configured token endpoint, cached
briefly in-process, and then applied to Azure Blob data-plane and Container Apps Jobs ARM calls.

Key classes:
- BackendConfigurationError: raised when required non-secret Azure resource config is missing.
- BackendRequestError: raised when an Azure REST call fails.
- TokenEndpoint: one resolved managed-identity token endpoint (URL, API version, headers).
- ManagedIdentityTokenProvider: cached managed-identity token provider.

Key functions:
- azure_http_request: perform one bounded-timeout HTTP request and return status/body.
- resolve_token_endpoint: pick the managed-identity flavor this runtime actually exposes.
- configured_url:

Notes:
- Full endpoints and resource audiences live in config/env; source only assembles paths from typed
settings so the no-hardcoding guard still owns environment-specific values.
- Two managed-identity flavors exist and the runtime decides which one applies. Azure Container
Apps does NOT serve IMDS: it injects a per-replica local token service (IDENTITY_ENDPOINT) plus a
platform-ROTATED anti-SSRF guard (IDENTITY_HEADER) sent as X-IDENTITY-HEADER. Because that header
rotates, both values are read from the process environment on every acquisition — a rotating value
cannot be pinned in YAML or stamped as a Terraform env var, so this is the one Azure input that
cannot come from typed settings. Everywhere else (VM/VMSS, or any host where IMDS is reachable)
keeps the configured IMDS URL with its `Metadata: true` header.
- Error messages intentionally avoid response bodies, headers, and requested URLs because they may
contain provider details that should stay in server logs only.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping

from pydantic import BaseModel, Field

from fraudlens_backend.settings import AppSettings

_HTTP_ERROR_FLOOR = 400
_HTTP_OK = 200
_TOKEN_REFRESH_SKEW_SECONDS = 60.0
_TOKEN_DEFAULT_TTL_SECONDS = 300.0

# Names of the env vars Azure Container Apps injects into every replica. These are the platform's
# contract, not FraudLens config: the endpoint is replica-local and the header is rotated by the
# platform, so neither can be declared in config/*.yaml or stamped by Terraform.
CONTAINER_APPS_ENDPOINT_ENV = "IDENTITY_ENDPOINT"
CONTAINER_APPS_HEADER_ENV = "IDENTITY_HEADER"
_CONTAINER_APPS_HEADER = "X-IDENTITY-HEADER"
_IMDS_METADATA_HEADER = "Metadata"


class BackendConfigurationError(RuntimeError):
    """A selected backend is missing required non-secret configuration."""


class BackendRequestError(RuntimeError):
    """A selected backend could not complete its Azure REST request."""


def _require(value: str | None, name: str) -> str:
    """Return a required setting value or raise a PHI-free configuration error."""
    if value is None or not value:
        raise BackendConfigurationError(f"missing required setting: {name}")
    return value


def _join_url(base: str, path: str) -> str:
    """Join a configured base endpoint and an already-escaped absolute path."""
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def azure_http_request(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout_seconds: float,
) -> tuple[int, bytes]:
    """Perform an HTTP request with a bounded timeout and return (status, body)."""
    request = urllib.request.Request(
        url,
        data=body,
        headers=dict(headers or {}),
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", getattr(response, "code", 0)))
            return status, response.read()
    except urllib.error.HTTPError as exc:
        if exc.code >= _HTTP_ERROR_FLOOR:
            raise BackendRequestError(f"Azure request failed with status {exc.code}") from exc
        raise
    except urllib.error.URLError as exc:
        raise BackendRequestError("Azure request failed before a response was received") from exc


class TokenEndpoint(BaseModel):
    """One resolved managed-identity token endpoint: where to ask, and how to prove the ask."""

    url: str = Field(..., description="Token endpoint URL, without the query string.")
    api_version: str = Field(..., description="Token API version this endpoint flavor expects.")
    headers: dict[str, str] = Field(..., description="Headers this endpoint flavor requires.")
    flavor: str = Field(..., description="Flavor name for PHI-free diagnostics.")


def resolve_token_endpoint(settings: AppSettings) -> TokenEndpoint:
    """Return the managed-identity endpoint this runtime exposes, preferring Container Apps.

    Container Apps advertises itself through two injected env vars and does not route to IMDS at
    all, so presence of BOTH is what selects that flavor. A partially injected environment falls
    back rather than sending a credential-less request the platform would reject.
    """
    endpoint = os.environ.get(CONTAINER_APPS_ENDPOINT_ENV, "").strip()
    header = os.environ.get(CONTAINER_APPS_HEADER_ENV, "").strip()
    if endpoint and header:
        return TokenEndpoint(
            url=endpoint,
            api_version=settings.azure_container_apps_identity_api_version,
            headers={_CONTAINER_APPS_HEADER: header},
            flavor="container_apps",
        )
    return TokenEndpoint(
        url=_require(
            settings.azure_managed_identity_token_url,
            "azure_managed_identity_token_url",
        ),
        api_version=settings.azure_managed_identity_api_version,
        headers={_IMDS_METADATA_HEADER: "true"},
        flavor="imds",
    )


class ManagedIdentityTokenProvider:
    """Managed-identity token provider with simple per-resource in-process caching."""

    def __init__(self, settings: AppSettings) -> None:
        """Store the settings that define the token endpoint and identity."""
        self._settings = settings
        self._cache: dict[str, tuple[str, float]] = {}

    def token(self, resource: str) -> str:
        """Return a bearer token for the configured resource/audience."""
        resource = _require(resource, "azure token resource")
        now = time.time()
        cached = self._cache.get(resource)
        if cached is not None and cached[1] - _TOKEN_REFRESH_SKEW_SECONDS > now:
            return cached[0]

        endpoint = resolve_token_endpoint(self._settings)
        query: dict[str, str] = {
            "api-version": endpoint.api_version,
            "resource": resource,
        }
        # Both flavors default to the SYSTEM-assigned identity when no id is supplied, which on a
        # user-assigned-only app resolves to an identity that does not exist. The client id is
        # injected by Terraform, so an omitted one is a deploy gap, not a mode.
        if self._settings.azure_managed_identity_client_id:
            query["client_id"] = self._settings.azure_managed_identity_client_id
        separator = "&" if "?" in endpoint.url else "?"
        url = f"{endpoint.url}{separator}{urllib.parse.urlencode(query)}"
        status, body = azure_http_request(
            method="GET",
            url=url,
            headers=endpoint.headers,
            timeout_seconds=self._settings.azure_rest_timeout_seconds,
        )
        if status != _HTTP_OK:
            raise BackendRequestError(f"Managed identity token request returned status {status}")
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise BackendRequestError("Managed identity token response was not JSON") from exc
        if not isinstance(payload, dict):
            raise BackendRequestError("Managed identity token response was not an object")
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise BackendRequestError("Managed identity token response did not include a token")
        expires_on = _expires_on(payload.get("expires_on"), now)
        self._cache[resource] = (access_token, expires_on)
        return access_token


def _expires_on(value: object, now: float) -> float:
    """Parse Azure's expires_on field, falling back to a short safe cache TTL."""
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.isdigit():
        return float(value)
    return now + _TOKEN_DEFAULT_TTL_SECONDS


def configured_url(base: str, path: str) -> str:
    """Expose URL joining to concrete backends while keeping endpoint config centralized."""
    return _join_url(base, path)

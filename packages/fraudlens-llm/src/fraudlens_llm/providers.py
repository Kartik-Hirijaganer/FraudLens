"""Summary: Provider governance and named-connection schema for the non-secret LLM registry.
A PROVIDER declares governance posture (region, retention, ZDR, training opt-out, permitted data
classes) and its default transport; a CONNECTION is a named route through that provider with its
own runtime-injected URL and key env vars plus mandatory request options. The split exists because
the quality-gated SAR cascade must reach two DIFFERENT self-hosted vLLM endpoints (`runpod-awq`
and `runpod-bf16`) that share one governance posture — with a single `vllm` entry both tiers
resolved to the same URL and the cascade could not work at all (release 0.5.0 AD-2.5). A
connection never weakens its provider's posture: it inherits every governance field unchanged and
may only add endpoint routing, request options such as zero-data-retention enforcement, and an
allowed-upstream list. Secrets stay in Infisical-provided environment variables.

Key classes:
- Protocol: Supported provider adapter protocols.
- ProviderConfig: Validated provider connection and governance metadata.
- ConnectionConfig: One named route through a provider with its own env references.
- Providers: Validated provider + connection registry wrapper.

Key functions:
- load_providers: Load and validate provider YAML.
- resolve_base_url: Resolve and validate a provider endpoint from config/environment.
- allows_data_class: Return whether a provider allows a data class.
- is_equal_or_stricter: Compare two provider governance postures.

Notes:
- Header validation rejects auth-like names and secret-like values.
- `Providers.route` returns the provider config a named connection resolves to, so callers never
  handle URLs or keys themselves and an unknown connection name fails closed.
"""

from __future__ import annotations

import ipaddress
import os
import re
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from fraudlens_llm.exceptions import CatalogError, ProviderNotConfiguredError
from fraudlens_llm.models import DataClass

if TYPE_CHECKING:
    from fraudlens_llm.settings import LlmSettings


class Protocol(StrEnum):
    """Supported SDK adapter protocols."""

    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"


_ENV_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SECRET_KEY_RE = re.compile(
    r"(?i)\b(passwd|password|secret|token|api[_-]?key|private[_-]?key|"
    r"access[_-]?key|client[_-]?secret|credential)\b"
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+|sk-[a-z0-9]|api[_-]?key|secret|token|password|"
    r"AIza[0-9A-Za-z_-]{12,}|xox[baprs]-)"
)
_HEADER_DENYLIST = {
    "authorization",
    "api-key",
    "x-api-key",
    "proxy-authorization",
    "cookie",
    "set-cookie",
}
_DATA_CLASS_RANK: dict[DataClass, int] = {
    DataClass.SYNTHETIC: 1,
    DataClass.DEIDENTIFIED: 2,
    DataClass.INTERNAL: 3,
    DataClass.RESTRICTED: 4,
}
_GLOBAL_REGION_RANK = 0
_LOCAL_REGION_RANK = 1
_RETENTION_PROVIDER_DEFAULT_DAYS = 10_000


class ProviderConfig(BaseModel):
    """Provider connection and governance metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol: Protocol = Field(..., description="Provider adapter protocol.")
    base_url: str | None = Field(
        default=None, description="HTTPS base URL for OpenAI-compatible providers."
    )
    base_url_env: str | None = Field(
        default=None,
        description="Optional uppercase env-var name containing a runtime base URL.",
    )
    allow_plain_http: bool = Field(
        default=False,
        description="Allow loopback HTTP from base_url_env outside production.",
    )
    api_key_env: str = Field(..., description="Environment variable name containing the API key.")
    timeout_s: float = Field(..., gt=0, le=600, description="Per-request timeout in seconds.")
    max_retries: int = Field(..., ge=0, le=10, description="SDK-native retry count.")
    headers: dict[str, str] = Field(default_factory=dict, description="Non-secret static headers.")
    request_options: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Mandatory non-secret request body options applied to every call.",
    )
    region: str = Field(..., min_length=1, description="Provider processing region.")
    data_retention: str = Field(..., min_length=1, description="Provider data retention policy.")
    zdr_supported: bool = Field(..., description="Whether zero-data-retention is supported.")
    training_opt_out: bool = Field(..., description="Whether training opt-out is active.")
    baa_required: bool = Field(..., description="Whether a BAA is required for restricted data.")
    allowed_data_classes: list[DataClass] = Field(
        ..., min_length=1, description="Data classes this provider may receive."
    )

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        """Require a complete HTTPS URL for static provider endpoints."""
        if value is not None:
            _validate_url(value, allow_loopback_http=False, environment="prod")
        return value

    @field_validator("api_key_env", "base_url_env")
    @classmethod
    def _validate_env_reference(cls, value: str | None) -> str | None:
        """Validate that connection fields name env vars rather than containing values."""
        if value is not None and not _ENV_VAR_RE.fullmatch(value):
            raise ValueError("connection env references must be uppercase environment names")
        return value

    @field_validator("headers")
    @classmethod
    def _validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        """Reject auth-like header names and secret-like header values."""
        for header_name, header_value in value.items():
            normalized = header_name.lower()
            if normalized in _HEADER_DENYLIST or _SECRET_KEY_RE.search(normalized):
                raise ValueError(f"header '{header_name}' is not allowed in providers.yml")
            if _SECRET_VALUE_RE.search(header_value):
                raise ValueError(f"header '{header_name}' appears to contain a secret")
        return value

    @model_validator(mode="after")
    def _validate_protocol_requirements(self) -> ProviderConfig:
        """Enforce protocol-specific connection rules."""
        if (
            self.protocol == Protocol.OPENAI_COMPATIBLE
            and self.base_url is None
            and self.base_url_env is None
        ):
            raise ValueError("base_url or base_url_env is required for openai_compatible providers")
        return self


class ConnectionConfig(BaseModel):
    """One named route through a provider, with its own runtime-injected env references."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(
        ..., min_length=1, description="Governing provider whose posture this route inherits."
    )
    base_url_env: str | None = Field(
        default=None, description="Uppercase env-var name holding this route's base URL."
    )
    api_key_env: str | None = Field(
        default=None, description="Uppercase env-var name holding this route's API key."
    )
    request_options: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Mandatory non-secret request options (e.g. zero-data-retention).",
    )
    allowed_upstreams: tuple[str, ...] = Field(
        default=(), description="Upstream route names this connection may be served by."
    )

    @field_validator("api_key_env", "base_url_env")
    @classmethod
    def _validate_env_reference(cls, value: str | None) -> str | None:
        """Validate that connection fields name env vars rather than containing values."""
        if value is not None and not _ENV_VAR_RE.fullmatch(value):
            raise ValueError("connection env references must be uppercase environment names")
        return value


_ProvidersData = dict[str, ProviderConfig]
_ConnectionsData = dict[str, ConnectionConfig]
_PROVIDERS_ADAPTER: TypeAdapter[_ProvidersData] = TypeAdapter(_ProvidersData)


class Providers(BaseModel):
    """Validated provider governance registry plus its named connection routes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    providers: _ProvidersData = Field(..., description="Provider configs keyed by name.")
    connections: _ConnectionsData = Field(
        default_factory=dict, description="Named routes keyed by connection name."
    )

    def get(self, provider: str) -> ProviderConfig:
        """Return a provider config or raise when the provider is reference-only."""
        config = self.providers.get(provider)
        if config is None:
            raise ProviderNotConfiguredError(f"Provider '{provider}' is not configured")
        return config

    def connection(self, name: str) -> ConnectionConfig:
        """Return a named connection or raise when the route is not registered."""
        route = self.connections.get(name)
        if route is None:
            raise ProviderNotConfiguredError(f"Connection '{name}' is not configured")
        return route

    def route(self, provider: str, connection: str | None) -> ProviderConfig:
        """Return the provider config a call resolves to, applying a named route's env overrides."""
        config = self.get(provider)
        if connection is None:
            return config
        route = self.connection(connection)
        if route.provider != provider:
            raise ProviderNotConfiguredError(
                f"Connection '{connection}' does not route to provider '{provider}'"
            )
        overrides: dict[str, object] = {
            key: value
            for key, value in (
                ("base_url_env", route.base_url_env),
                ("api_key_env", route.api_key_env),
            )
            if value is not None
        }
        if route.request_options:
            overrides["request_options"] = {**config.request_options, **route.request_options}
        return config.model_copy(update=overrides) if overrides else config


def resolve_base_url(
    config: ProviderConfig,
    settings: LlmSettings | None = None,
) -> str:
    """Resolve an endpoint with env precedence and production-safe transport rules."""
    configured_value: str | None = None
    if config.base_url_env is not None:
        configured_value = os.environ.get(config.base_url_env)
        if configured_value is not None:
            configured_value = configured_value.strip()
    if configured_value is None:
        configured_value = config.base_url
    if configured_value is None:
        raise ProviderNotConfiguredError("Provider base URL is not configured")

    if settings is None:
        from fraudlens_llm.settings import get_llm_settings  # noqa: PLC0415

        settings = get_llm_settings()
    return _validate_url(
        configured_value,
        allow_loopback_http=config.allow_plain_http,
        environment=settings.environment,
    )


def _validate_url(value: str, *, allow_loopback_http: bool, environment: str) -> str:
    """Validate one resolved absolute endpoint without exposing it in errors."""
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("provider base URL must be an absolute URL without credentials")
    if parsed.scheme == "https":
        return value
    if (
        parsed.scheme == "http"
        and allow_loopback_http
        and environment != "prod"
        and _is_loopback_host(parsed.hostname)
    ):
        return value
    raise ValueError("provider base URL must use HTTPS or permitted non-production loopback HTTP")


def _is_loopback_host(host: str) -> bool:
    """Return whether a URL host is unambiguously local to this process."""
    if host.rstrip(".").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def allows_data_class(config: ProviderConfig, data_class: DataClass) -> bool:
    """Return whether a provider allows the requested data class."""
    return data_class in config.allowed_data_classes


def is_equal_or_stricter(current: ProviderConfig, candidate: ProviderConfig) -> bool:
    """Return whether candidate governance posture is no weaker than current."""
    if current.zdr_supported and not candidate.zdr_supported:
        return False
    if current.training_opt_out and not candidate.training_opt_out:
        return False
    if not _region_equal_or_stricter(current.region, candidate.region):
        return False
    return _retention_days(candidate.data_retention) <= _retention_days(current.data_retention)


def _region_equal_or_stricter(current: str, candidate: str) -> bool:
    """Return whether candidate region posture is no weaker than current."""
    current_rank = _region_rank(current)
    candidate_rank = _region_rank(candidate)
    if candidate_rank < current_rank:
        return False
    if current_rank == _LOCAL_REGION_RANK and candidate_rank == _LOCAL_REGION_RANK:
        return candidate.lower() == current.lower()
    return True


def _region_rank(region: str) -> int:
    """Rank global as weaker than named regional processing."""
    return _GLOBAL_REGION_RANK if region.lower() == "global" else _LOCAL_REGION_RANK


def _retention_days(value: str) -> int:
    """Convert provider retention metadata into a conservative sortable value."""
    normalized = value.strip().lower()
    if normalized in {"none", "0d", "zero"}:
        return 0
    if normalized.endswith("d") and normalized[:-1].isdigit():
        return int(normalized[:-1])
    return _RETENTION_PROVIDER_DEFAULT_DAYS


def load_providers(path: str | Path) -> Providers:
    """Load and validate the provider + connection YAML, wrapping parser/validation errors."""
    providers_path = Path(path)
    try:
        raw: Any = yaml.safe_load(providers_path.read_text(encoding="utf-8")) or {}
        registry = Providers.model_validate(
            raw
            if isinstance(raw, dict) and "providers" in raw
            else {"providers": _PROVIDERS_ADAPTER.validate_python(raw)}
        )
    except (OSError, TypeError, yaml.YAMLError, ValidationError) as exc:
        raise CatalogError(f"Failed to load LLM providers from {providers_path}") from exc
    for name, route in registry.connections.items():
        if route.provider not in registry.providers:
            raise CatalogError(f"Connection '{name}' names an unconfigured provider")
    return registry

"""Summary: Provider connection and governance schema for the non-secret LLM
provider registry. Provider configs hold endpoints, env-var key references, retry
settings, and data-governance posture while secrets stay in Infisical-provided
environment variables.

Key classes:
- Protocol: Supported provider adapter protocols.
- ProviderConfig: Validated provider connection and governance metadata.
- Providers: Validated provider registry wrapper.

Key functions:
- load_providers: Load and validate provider YAML.
- allows_data_class: Return whether a provider allows a data class.
- is_equal_or_stricter: Compare two provider governance postures.

Notes:
- Header validation rejects auth-like names and secret-like values.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from fraudlens_llm.exceptions import CatalogError, ProviderNotConfiguredError
from fraudlens_llm.models import DataClass


class Protocol(StrEnum):
    """Supported SDK adapter protocols."""

    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"


_API_KEY_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
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
    api_key_env: str = Field(..., description="Environment variable name containing the API key.")
    timeout_s: float = Field(..., gt=0, le=600, description="Per-request timeout in seconds.")
    max_retries: int = Field(..., ge=0, le=10, description="SDK-native retry count.")
    headers: dict[str, str] = Field(default_factory=dict, description="Non-secret static headers.")
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
        """Validate base URLs when present."""
        if value is not None and not value.startswith("https://"):
            raise ValueError("base_url must use https://")
        return value

    @field_validator("api_key_env")
    @classmethod
    def _validate_api_key_env(cls, value: str) -> str:
        """Validate that api_key_env is an env-var reference, not a value."""
        if not _API_KEY_ENV_RE.fullmatch(value):
            raise ValueError("api_key_env must be an uppercase environment variable name")
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
        if self.protocol == Protocol.OPENAI_COMPATIBLE and self.base_url is None:
            raise ValueError("base_url is required for openai_compatible providers")
        return self


_ProvidersData = dict[str, ProviderConfig]
_PROVIDERS_ADAPTER: TypeAdapter[_ProvidersData] = TypeAdapter(_ProvidersData)


class Providers(BaseModel):
    """Validated provider registry wrapper."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    providers: _ProvidersData = Field(..., description="Provider configs keyed by name.")

    def get(self, provider: str) -> ProviderConfig:
        """Return a provider config or raise when the provider is reference-only."""
        config = self.providers.get(provider)
        if config is None:
            raise ProviderNotConfiguredError(f"Provider '{provider}' is not configured")
        return config


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
    """Load and validate provider YAML, wrapping parser/validation errors."""
    providers_path = Path(path)
    try:
        raw: Any = yaml.safe_load(providers_path.read_text(encoding="utf-8"))
        data = _PROVIDERS_ADAPTER.validate_python(raw)
        return Providers(providers=data)
    except (OSError, TypeError, yaml.YAMLError, ValidationError) as exc:
        raise CatalogError(f"Failed to load LLM providers from {providers_path}") from exc

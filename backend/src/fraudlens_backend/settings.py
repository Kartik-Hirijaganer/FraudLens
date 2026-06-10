"""Summary: Application settings for the FraudLens backend, built on
pydantic-settings. Configuration is layered (lowest to highest precedence):
config/default.yaml -> config/<environment>.yaml -> FRAUDLENS_* environment
variables -> explicit constructor args. Only NON-secret config lives in these
sources; secrets are fetched from Infisical at runtime (Golden Rule 2). The model
uses extra="forbid" so an unknown key fails fast rather than being ignored.

Key classes:
- AppSettings: the validated, frozen settings model for the service.

Key functions:
- get_settings: process-wide cached accessor used as a FastAPI dependency.

Notes:
- The dev auth-bypass is gated by `is_dev_bypass_enabled`, which is False whenever
  environment == "prod" REGARDLESS of the flag — so prod cannot be bypassed.
- The config directory is discovered via FRAUDLENS_CONFIG_DIR, then by walking up
  from the CWD / this file looking for config/default.yaml (works in src layout,
  editable installs, and the Docker image where FRAUDLENS_CONFIG_DIR=/app/config).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

Environment = Literal["dev", "prod"]


def _find_config_dir() -> Path:
    """Locate the config/ directory containing default.yaml; fail soft if absent."""
    override = os.environ.get("FRAUDLENS_CONFIG_DIR")
    if override:
        return Path(override)
    for start in (Path.cwd(), Path(__file__).resolve()):
        for parent in (start, *start.parents):
            candidate = parent / "config"
            if (candidate / "default.yaml").is_file():
                return candidate
    return Path(__file__).resolve().parents[3] / "config"  # pragma: no cover - last resort


def _active_environment() -> str:
    """Return the active environment name from the env var (default 'dev')."""
    return os.environ.get("FRAUDLENS_ENVIRONMENT", "dev")


class AppSettings(BaseSettings):
    """Validated, immutable application settings loaded from YAML + env."""

    model_config = SettingsConfigDict(
        env_prefix="FRAUDLENS_",
        extra="forbid",
        frozen=True,
        case_sensitive=False,
    )

    app_name: str = Field(default="FraudLens", description="Human-readable service name.")
    environment: Environment = Field(
        default="dev",
        description="Active deployment environment; gates the auth dev-bypass.",
    )
    log_level: str = Field(default="INFO", description="Python logging level name.")
    api_v1_prefix: str = Field(
        default="/api/v1",
        description="Prefix for business APIs; ops endpoints stay unprefixed.",
    )
    request_id_header: str = Field(
        default="X-Request-Id",
        description="Response header carrying the per-request correlation id.",
    )
    auth_dev_bypass: bool = Field(
        default=False,
        description="Dev-only auth bypass; honored only when environment != 'prod'.",
    )

    @property
    def is_dev_bypass_enabled(self) -> bool:
        """True only when NOT in prod and the bypass flag is set (fails closed in prod)."""
        return self.environment != "prod" and self.auth_dev_bypass

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Layer YAML config under env vars and constructor args (highest priority first)."""
        config_dir = _find_config_dir()
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
        env_yaml = config_dir / f"{_active_environment()}.yaml"
        if env_yaml.is_file():
            sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=env_yaml))
        default_yaml = config_dir / "default.yaml"
        if default_yaml.is_file():
            sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=default_yaml))
        return tuple(sources)


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return the process-wide cached settings instance (FastAPI dependency)."""
    return AppSettings()

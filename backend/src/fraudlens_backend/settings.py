"""Summary: Application settings for the FraudLens backend, built on
pydantic-settings. Configuration is layered (lowest to highest precedence):
config/default.yaml -> config/<environment>.yaml -> FRAUDLENS_* environment
variables -> explicit constructor args. Only NON-secret config lives in these
sources; secrets are fetched from Infisical at runtime (Golden Rule 2). The model
uses extra="forbid" so an unknown key fails fast rather than being ignored.

Key classes:
- AppSettings: the validated, frozen settings model for the service.

Key functions:
- find_config_dir: locate the config/ directory (env override, else walk up).
- get_settings: process-wide cached accessor used as a FastAPI dependency.

Notes:
- The dev auth-bypass is gated by `is_dev_bypass_enabled`, which is False whenever
  environment == "prod" REGARDLESS of the flag — so prod cannot be bypassed.
- The config directory is discovered via FRAUDLENS_CONFIG_DIR, then by walking up
  from the CWD / this file looking for config/default.yaml (works in src layout,
  editable installs, and the Docker image where FRAUDLENS_CONFIG_DIR=/app/config).
- Boot-critical edge config (CORS allowlist, rate limits, security headers, backend
  selectors, LLM mode) lives HERE — typed YAML/env loaded at startup, never the DB —
  so the gateway/security posture is fully determined before DB readiness (plan §12.3).
- `database_url` is read from the unprefixed DATABASE_URL env (Infisical-injected in
  prod, a local docker URL in dev) as well as FRAUDLENS_DATABASE_URL; it never lives
  in committed YAML.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

Environment = Literal["dev", "prod", "staging"]
StorageBackend = Literal["local", "azure_blob"]
QueueBackend = Literal["local", "container_apps_jobs"]
LlmMode = Literal["mock", "live"]

# Safe defaults for the always-on security headers (no CSP — Phase 13 adds CSP, which
# needs care around the Swagger UI CDN). These are overridable via config (plan §12.3).
_DEFAULT_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


def find_config_dir() -> Path:
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
        populate_by_name=True,
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

    # --- Gateway edge: CORS allowlist (boot-critical; origins set in config, not source) ---
    cors_allow_origins: list[str] = Field(
        default_factory=list,
        description="Exact allowed CORS origins; set per-env in config (never hardcoded).",
    )
    cors_allow_methods: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Allowed CORS methods for the gateway edge.",
    )
    cors_allow_headers: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Allowed CORS request headers for the gateway edge.",
    )
    cors_allow_credentials: bool = Field(
        default=False,
        description="Whether the gateway allows credentialed CORS requests.",
    )

    # --- Gateway edge: rate limiting (fixed-window, per client) ---
    rate_limit_enabled: bool = Field(
        default=True,
        description="Enable the gateway fixed-window rate limiter.",
    )
    rate_limit_requests: int = Field(
        default=120,
        gt=0,
        description="Max requests per client within the window before 429.",
    )
    rate_limit_window_seconds: float = Field(
        default=60.0,
        gt=0,
        description="Length of the rate-limit fixed window, in seconds.",
    )

    # --- Gateway edge: security response headers (config-overridable safe defaults) ---
    security_headers: dict[str, str] = Field(
        default_factory=lambda: dict(_DEFAULT_SECURITY_HEADERS),
        description="Security response headers applied to every gateway response.",
    )
    gateway_routes_file: str | None = Field(
        default=None,
        description="Override path to the gateway routing table; else discovered under config/.",
    )

    # --- Config-driven backends (plan §12.3): local for the one-command demo, cloud later ---
    storage_backend: StorageBackend = Field(
        default="local",
        description="Artifact/PDF storage backend selector (local-FS vs Azure Blob).",
    )
    storage_local_dir: str = Field(
        default=".local/artifacts",
        description="Root directory for the local-FS storage backend (gitignored).",
    )
    queue_backend: QueueBackend = Field(
        default="local",
        description="Background-job backend selector (local runner vs Container Apps Jobs).",
    )
    llm_mode: LlmMode = Field(
        default="mock",
        description="SAR drafter mode: 'mock' needs no keys/cost; 'live' calls a provider.",
    )
    model_artifacts_dir: str = Field(
        default="data/models",
        description="Root dir (by version label) for model artifact bundles; the committed "
        "fixture lives here, candidates are written here, prod points it at Blob.",
    )

    # --- RAG over FinCEN/BSA (plan §16 Phase 6; config-driven, never hardcoded) ---
    rag_corpus_dir: str = Field(
        default="data/regulations",
        description="Committed source corpus dir (`*.md` provisions) ingest builds the index from.",
    )
    rag_index_dir: str = Field(
        default=".local/chroma",
        description="ChromaDB index dir (built by ingest-rag; baked into the prod image).",
    )
    rag_collection: str = Field(
        default="fincen_bsa",
        description="ChromaDB collection name holding the embedded regulatory chunks.",
    )
    rag_version: str = Field(
        default="rag-v1",
        description="Corpus/index version recorded on each retrieval for the audit trail.",
    )
    rag_index_required: bool = Field(
        default=False,
        description="When true, a missing/empty RAG index fails /readyz (prod bakes the index).",
    )

    # --- Database (secret value via env; non-secret local docker URL in dev) ---
    database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("database_url", "DATABASE_URL", "FRAUDLENS_DATABASE_URL"),
        description="Async SQLAlchemy URL (asyncpg driver); read from env, never committed YAML.",
    )
    db_connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Timeout for the /readyz database connectivity probe, in seconds.",
    )

    # --- Ingestion limits (plan §16 Phase 3; config-driven, never hardcoded) ---
    ingest_max_batch_size: int = Field(
        default=500,
        gt=0,
        description="Max transactions accepted in one /transactions/batch request.",
    )
    ingest_csv_max_bytes: int = Field(
        default=5_242_880,
        gt=0,
        description="Max accepted /transactions/upload body size in bytes (413 above it).",
    )
    ingest_csv_max_rows: int = Field(
        default=10_000,
        gt=0,
        description="Max data rows accepted in one CSV upload (413 above it).",
    )
    ingest_sample_errors_limit: int = Field(
        default=10,
        ge=0,
        description="Max per-row rejection samples returned by batch/CSV ingest.",
    )
    client_error_max_message_length: int = Field(
        default=2_000,
        gt=0,
        description="Max length of a client-error report message before truncation.",
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
        config_dir = find_config_dir()
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

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

# Safe defaults for the always-on static security headers. The Content-Security-Policy is
# handled separately (it is path-aware: strict on the API, relaxed on the docs UI — see
# middleware/security.py). All values are overridable via config (plan §12.3).
_DEFAULT_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}

# Strict default Content-Security-Policy for the JSON API surface: nothing loads, frames,
# or submits. The interactive docs UI relaxes this via content_security_policy_docs (which
# carries the documentation CDN origin and therefore lives in config, not source — §12.3).
_DEFAULT_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)


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
    auth_dev_bypass_role: Literal["analyst", "reviewer", "admin"] = Field(
        default="admin",
        description="RBAC role the dev bypass mints (default admin so local-demo can drive the "
        "model lifecycle); honored only when the bypass is enabled, so it is prod-inert.",
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
        description="Static security response headers applied to every gateway response.",
    )
    csp_enabled: bool = Field(
        default=True,
        description="Stamp a Content-Security-Policy header on every gateway response.",
    )
    content_security_policy: str = Field(
        default=_DEFAULT_CONTENT_SECURITY_POLICY,
        description="Strict CSP applied to the API surface (config-overridable, plan §12.3).",
    )
    content_security_policy_docs: str = Field(
        default="",
        description="Relaxed CSP for the interactive docs UI (Swagger/ReDoc CDN); set in config. "
        "Empty falls back to the strict policy so the API surface is never weakened.",
    )
    docs_ui_paths: list[str] = Field(
        default_factory=lambda: ["/docs", "/redoc"],
        description="Paths serving the interactive docs UI that receive the relaxed CSP.",
    )
    gateway_routes_file: str | None = Field(
        default=None,
        description="Override path to the gateway routing table; else discovered under config/.",
    )

    # --- Observability (plan §11.5, §16 Phase 12): optional OTel export, OFF by default ---
    telemetry_enabled: bool = Field(
        default=False,
        description="Enable the optional OpenTelemetry → Azure Monitor exporter; OFF by default "
        "(stdout JSON → Log Analytics is the v1 telemetry path, the live exporter lands in P14).",
    )
    telemetry_service_name: str = Field(
        default="fraudlens-backend",
        description="Service name reported by telemetry export when enabled (App Insights / OTel).",
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
    client_error_rate_limit_requests: int = Field(
        default=60,
        gt=0,
        description="Per-client request budget for the telemetry client-error sink within the "
        "rate-limit window — a stricter per-route limit layered on the global gateway limiter as "
        "defense-in-depth for this abuse-prone, client-driven endpoint (plan §16 Phase 13).",
    )

    # --- Investigation pipeline (plan §16 Phase 8; config-driven, never hardcoded) ---
    investigation_history_window_hours: int = Field(
        default=168,
        gt=0,
        description="Same-account history lookback fed to the rules engine + features (covers the "
        "widest built-in rule window, structuring at 7 days).",
    )
    investigation_history_max: int = Field(
        default=100,
        gt=0,
        description="Cap on same-account history rows loaded per investigation (bounds the query).",
    )
    investigation_rag_top_k: int = Field(
        default=4,
        gt=0,
        description="How many FinCEN/BSA chunks the investigation retrieves for citations.",
    )
    investigation_idempotency_cache_size: int = Field(
        default=1024,
        gt=0,
        description="Max retained Idempotency-Key→runId entries in the in-process run manager "
        "(LRU-bounded; the single-replica dedupe window, ADR-016).",
    )

    # --- Alerts & review workflow (plan §16 Phase 9; config-driven, never hardcoded) ---
    review_low_confidence_margin: float = Field(
        default=0.1,
        gt=0,
        le=0.5,
        description="Half-width around the 0.5 decision boundary inside which a run's model "
        "probability force-flags the alert as low-confidence for review (plan §8.5).",
    )
    sar_pdf_max_attempts: int = Field(
        default=3,
        gt=0,
        description="Max attempts the deferred SAR-PDF task makes before giving up; PDF "
        "generation is best-effort and never blocks SAR approval (plan §16 Phase 9).",
    )

    # --- Model lifecycle / MLOps (plan §16 Phase 10, §9.4, §10.5.1; config-driven) ---
    retrain_min_labels_total: int = Field(
        default=10,
        gt=0,
        description="Min matured reviewed labels (any class) before a retrain is eligible; below "
        "it the trigger returns insufficient_matured_labels (plan §9.4). Dev-friendly default.",
    )
    retrain_min_labels_per_class: int = Field(
        default=2,
        gt=0,
        description="Min matured labels required for EACH of the fraud/benign classes before a "
        "retrain is eligible (guards a one-sided training set, plan §9.4).",
    )
    retrain_tenant_slices: int = Field(
        default=2,
        ge=2,
        description="Deterministic holdout partitions used as per-tenant evaluation slices when "
        "computing the §9.4 per-tenant slice gate (synthetic-data MLOps stand-in for agencies).",
    )
    canary_guard_min_samples: int = Field(
        default=20,
        gt=0,
        description="Min inference samples per arm (active/canary) before the canary auto-abort "
        "guard will act on a deviation (the §10.5.1 min-sample window).",
    )
    canary_guard_max_deviation: float = Field(
        default=0.20,
        gt=0,
        le=1.0,
        description="Max absolute deviation between the canary's and active's mean predicted "
        "probability (alert-rate/precision proxy) before auto-abort → rollback (plan §10.5.1).",
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

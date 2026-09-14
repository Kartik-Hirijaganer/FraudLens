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
- Candidate scoring without a deployment is likewise gated by
  `is_candidate_scoring_fallback_enabled` and is always inert in production.
- The config directory is discovered via FRAUDLENS_CONFIG_DIR, then by walking up
  from the CWD / this file looking for config/default.yaml (works in src layout,
  editable installs, and the Docker image where FRAUDLENS_CONFIG_DIR=/app/config).
- Boot-critical edge config (CORS allowlist, rate limits, security headers, backend
  selectors, LLM mode) lives HERE — typed YAML/env loaded at startup, never the DB —
  so the gateway/security posture is fully determined before DB readiness (plan §12.3).
- `database_url` is read from the unprefixed DATABASE_URL env (Infisical-injected in
  prod, a local docker URL in dev) as well as FRAUDLENS_DATABASE_URL; it never lives
  in committed YAML.
- `infisical_secrets_delivery` + `infisical_required_env_keys` declare HOW secrets reach
  the process (the service never calls Infisical itself) and WHICH injected env-var names
  the /readyz infisical check must find; declaring injection without any key to verify is
  rejected at boot, so the readiness gate can never be satisfied vacuously.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from fraudlens_backend.settings_cloud import AzureRuntimeFields
from fraudlens_backend.settings_defaults import (
    Environment,
    LlmMode,
    QueueBackend,
    RagEmbeddingMode,
    SecretsDelivery,
    StorageBackend,
)
from fraudlens_backend.settings_gateway import GatewayRuntimeFields
from fraudlens_backend.settings_investigations import InvestigationRuntimeFields

__all__ = ["AppSettings", "_config_anchored", "find_config_dir", "get_settings"]


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


def _config_anchored(path_value: str) -> Path:
    """Resolve a relative path below config/, rejecting absolute paths and traversal."""
    path = Path(path_value)
    if path.is_absolute():
        raise ValueError("Configuration path must be relative to the config directory")
    base = find_config_dir().resolve()
    resolved = (base / path).resolve()
    if not resolved.is_relative_to(base):
        raise ValueError("Configuration path must remain below the config directory")
    return resolved


def _active_environment() -> str:
    """Return the active environment name from the env var (default 'dev')."""
    return os.environ.get("FRAUDLENS_ENVIRONMENT", "dev")


class AppSettings(
    InvestigationRuntimeFields,
    GatewayRuntimeFields,
    AzureRuntimeFields,
    BaseSettings,
):
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
    auth_dev_bypass_role: Literal["auditor", "analyst", "reviewer", "admin"] = Field(
        default="admin",
        description="RBAC role the dev bypass mints (default admin so local-demo can drive the "
        "model lifecycle); honored only when the bypass is enabled, so it is prod-inert.",
    )
    auth_jwks_url: str | None = Field(
        default=None,
        description=(
            "Supabase Auth JWKS URL for asymmetric (ES256/RS256) access-token verification; "
            "unset fails closed."
        ),
    )
    auth_jwt_issuer: str | None = Field(
        default=None,
        description=(
            "Expected JWT issuer; unset skips issuer validation for local/integration tests."
        ),
    )
    auth_jwt_audience: str | None = Field(
        default=None,
        description=(
            "Expected JWT audience; unset skips audience validation for local/integration tests."
        ),
    )
    auth_jwt_algorithm: Literal["ES256", "RS256"] = Field(
        default="ES256",
        description=(
            "JWT signing algorithm accepted from the configured JWKS. Supabase Auth signs ES256 "
            "(asymmetric) by default; RS256 is also accepted (e.g. a rotated RSA signing key)."
        ),
    )
    auth_agency_claim: str = Field(
        default="agency_id",
        description="JWT claim containing the tenant agency id.",
    )
    auth_role_claim: str = Field(
        default="user_role",
        description=(
            "JWT claim containing the FraudLens RBAC role. Supabase's built-in top-level "
            "`role` claim is reserved for `authenticated`, so FraudLens uses `user_role`."
        ),
    )
    supabase_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("supabase_url", "SUPABASE_URL", "FRAUDLENS_SUPABASE_URL"),
        description=(
            "Supabase project URL used by admin-invite provisioning; non-secret and read from env."
        ),
    )
    supabase_service_role_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "supabase_service_role_key",
            "SUPABASE_SERVICE_ROLE_KEY",
            "FRAUDLENS_SUPABASE_SERVICE_ROLE_KEY",
        ),
        description=(
            "Supabase service-role key for admin user invites; secret from Infisical /backend."
        ),
    )
    bootstrap_admin_user_id: str | None = Field(
        default=None,
        description=(
            "Optional first-admin auth.users id for scripts/seed.py bootstrap reconciliation."
        ),
    )
    bootstrap_admin_email: str | None = Field(
        default=None,
        description="Optional first-admin email for scripts/seed.py bootstrap reconciliation.",
    )
    bootstrap_admin_display_name: str = Field(
        default="Bootstrap Admin",
        description="Display name used when scripts/seed.py upserts the optional first admin.",
    )
    portfolio_demo_enabled: bool = Field(
        default=False,
        description="Enable the config-driven portfolio demo story; a security gate that fails "
        "closed in code, so a missing YAML key leaves it off (like auth_dev_bypass).",
    )
    portfolio_demo_config_file: str = Field(
        default="portfolio-demo.yaml",
        description="Portfolio-demo story config FILENAME, resolved relative to find_config_dir(); "
        "absolute paths and upward traversal are rejected by the loader.",
    )
    demo_auth_password: str | None = Field(
        default=None,
        description="Public synthetic demo credential supplied by FRAUDLENS_DEMO_AUTH_PASSWORD / "
        "Infisical; deliberately non-secret demo data, but never an inline YAML value.",
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
    local_job_execute_on_submit: bool = Field(
        default=False,
        description="When true, the local job backend executes known job commands synchronously "
        "after submission. Enabled by local-demo for browser UAT; off in hermetic tests.",
    )
    local_retrain_command: list[str] = Field(
        default_factory=lambda: ["uv", "run", "python", "scripts/retrain.py"],
        description="Command the local job backend runs for a retrain submission.",
    )
    llm_mode: LlmMode = Field(
        default="mock",
        description="SAR drafter mode: 'mock' needs no keys/cost; 'live' calls a provider.",
    )
    sar_config_file: str = Field(
        default="llm/sar.yml",
        description="SAR model-routing config resolved below the config directory.",
    )
    multi_agent_sar_enabled: bool = Field(
        default=False,
        description=(
            "Process-level gate for bounded multi-agent SAR drafting; the feature is active only "
            "when the tenant-scoped system_config flag is also enabled."
        ),
    )
    multi_agent_config_file: str = Field(
        default="llm/agents.yml",
        description=(
            "Multi-agent configuration filename resolved below the config directory; absolute "
            "paths and upward traversal are rejected by the loader."
        ),
    )
    model_artifacts_dir: str = Field(
        default="data/models",
        description="Root dir (by version label) for model artifact bundles; the committed "
        "fixture lives here, candidates are written here, prod points it at Blob.",
    )
    allow_candidate_scoring_in_dev: bool = Field(
        default=False,
        description="Allow scoring with the newest candidate when no deployment exists; honored "
        "only outside production for explicit live-local model evaluation.",
    )
    aml_data_dir: str = Field(
        default=".local/aml_data",
        description="Root dir for downloaded real AML training datasets (e.g. IBM AML-Data); "
        "relative paths anchor to the repo root like model_artifacts_dir. Gitignored and "
        "training-time only — raw data is never committed or served (real-AML plan Phase 1).",
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
    rag_embedding_mode: RagEmbeddingMode = Field(
        default="offline",
        description="RAG embedder mode: deterministic hashing or live OpenRouter embeddings.",
    )
    rag_version: str = Field(
        default="rag-v1",
        description="Offline corpus/index version; live mode reads its version from llm/rag.yml.",
    )
    rag_index_required: bool = Field(
        default=False,
        description="When true, a missing/empty RAG index fails /readyz (prod bakes the index).",
    )

    # --- Infisical secret delivery (Golden Rule 3): the service NEVER calls Infisical at
    # runtime. Secrets arrive as process environment, injected by `infisical run` locally, the
    # Infisical GitHub action in CI, Terraform-wired Container Apps secrets, or the Infisical
    # Kubernetes operator. These keys declare that contract so /readyz can verify the injection
    # actually happened instead of probing a service that is not in any request path.
    infisical_secrets_delivery: SecretsDelivery = Field(
        default="unconfigured",
        description="How Infisical secrets reach this process. 'unconfigured' declares no "
        "delivery mechanism, so the /readyz infisical check reports 'skipped'; "
        "'externally_injected' declares that a CLI/CI job/deploy platform injects them as env, "
        "so the check verifies every infisical_required_env_keys name is present and non-blank.",
    )
    infisical_required_env_keys: list[str] = Field(
        default_factory=list,
        description="Environment-variable NAMES (never values) the Infisical injection must "
        "supply; the /readyz infisical check reports 'down' when any is missing or blank, so a "
        "broken secret sync fails readiness instead of serving errors. Must be non-empty when "
        "infisical_secrets_delivery is 'externally_injected' (an injection claim with nothing to "
        "verify is rejected at boot).",
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

    @model_validator(mode="after")
    def _require_verifiable_secret_injection(self) -> AppSettings:
        """Reject an 'externally_injected' declaration with nothing to verify (fails closed)."""
        declared_injection = self.infisical_secrets_delivery == "externally_injected"
        if declared_injection and not self.infisical_required_env_keys:
            raise ValueError(
                "infisical_required_env_keys must list at least one env-var name when "
                "infisical_secrets_delivery is 'externally_injected'"
            )
        return self

    @property
    def is_dev_bypass_enabled(self) -> bool:
        """True only when NOT in prod and the bypass flag is set (fails closed in prod)."""
        return self.environment != "prod" and self.auth_dev_bypass

    @property
    def is_candidate_scoring_fallback_enabled(self) -> bool:
        """True only outside prod when candidate fallback was explicitly enabled."""
        return self.environment != "prod" and self.allow_candidate_scoring_in_dev

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
        del dotenv_settings, file_secret_settings
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

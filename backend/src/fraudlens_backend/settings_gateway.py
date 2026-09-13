"""Summary: Boot-critical gateway and telemetry fields mixed into AppSettings.

Key classes:
- GatewayRuntimeFields: typed CORS, rate-limit, security-header, and telemetry settings.

Key functions:
- (none)

Notes:
- Keeping these fields in one model makes the edge posture auditable before database readiness.
"""

from pydantic import BaseModel, Field

from fraudlens_backend.settings_defaults import (
    DEFAULT_CONTENT_SECURITY_POLICY,
    DEFAULT_SECURITY_HEADERS,
)


class GatewayRuntimeFields(BaseModel):
    """Boot-critical gateway and telemetry settings shared by AppSettings."""

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
    security_headers: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_SECURITY_HEADERS),
        description="Static security response headers applied to every gateway response.",
    )
    csp_enabled: bool = Field(
        default=True,
        description="Stamp a Content-Security-Policy header on every gateway response.",
    )
    content_security_policy: str = Field(
        default=DEFAULT_CONTENT_SECURITY_POLICY,
        description="Strict CSP applied to the API surface (config-overridable).",
    )
    content_security_policy_docs: str = Field(
        default="",
        description="Relaxed CSP for the interactive docs UI; empty keeps the strict policy.",
    )
    docs_ui_paths: list[str] = Field(
        default_factory=lambda: ["/docs", "/redoc"],
        description="Interactive documentation paths that receive the relaxed CSP.",
    )
    gateway_routes_file: str | None = Field(
        default=None,
        description="Override path to the gateway routing table; else discovered under config/.",
    )
    telemetry_enabled: bool = Field(
        default=False,
        description="Enable the optional OpenTelemetry exporter; disabled by default.",
    )
    telemetry_service_name: str = Field(
        default="fraudlens-backend",
        description="Service name reported by telemetry export when enabled.",
    )

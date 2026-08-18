"""Summary: The observability module (plan §11.3/§11.5/§11.7, §16 Phase 12). It centralizes
the PHI-free structured *events* the rest of §11.1's logging pipeline carries — security events
(`auth_fail`/`tenant_mismatch`/`rate_limited`/`guardrail_block`) and LLM-call cost/usage records —
so every call site emits the same stable keys through the SAME structlog pipeline (the redaction
processor in `middleware/logging.py` is a second, independent net). `init_telemetry` is the
config-gated seam for the optional OpenTelemetry → Azure Monitor / Application Insights exporter:
it is OFF by default (`telemetry_enabled=false`) and, in v1, only records the intent — Container
Apps streams stdout JSON to Log Analytics regardless (§11.5), and the live exporter is wired with
the Azure deploy (Phase 14), exactly like the scaffolded-but-inert Terraform. These helpers NEVER
emit PHI, secrets, tokens, or prompt/response content — only ids, counts, costs, and model/prompt
provenance (plan §7.4, §11.3 "never log").

Key classes:
- (none)

Key functions:
- init_telemetry: config-gated OTel/App Insights init (off by default); returns whether enabled.
- log_security_event: emit a PHI-free security event at WARNING (auth_fail/tenant_mismatch/...).
- log_llm_call: emit a PHI-free LLM-call cost/usage event (model/prompt provenance + tokens + cost).

Notes:
- `request_id`/`agency_id`/`user_id` ride the structlog contextvars bound by the gateway + the
  tenant dependency (plan §11.4), so security events emitted inside a request are auto-correlated;
  callers may pass them explicitly for events raised outside a request context (background jobs).
- `cost_usd` is rendered as a string so the logged value matches the `sar_drafts.cost_usd`
  NUMERIC exactly (no binary-float drift in cost dashboards, plan §11.5).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fraudlens_backend.middleware.logging import APP_LOGGER_NAME, get_logger
from fraudlens_backend.settings import AppSettings

# Dedicated logger names so security + LLM-cost events are filterable in Log Analytics without
# parsing the message (they still flow through the one redaction pipeline, plan §11.1).
SECURITY_LOGGER_NAME = "fraudlens.security"
LLM_LOGGER_NAME = "fraudlens.llm"

# The security-event vocabulary (plan §11.3) — kept here so call sites and the observability
# runbook share one canonical set (no duplication, rule 5).
SECURITY_EVENTS: frozenset[str] = frozenset(
    {"auth_fail", "tenant_mismatch", "rate_limited", "guardrail_block"}
)


def init_telemetry(settings: AppSettings) -> bool:
    """Initialize optional telemetry export (OTel → Azure Monitor); OFF by default (plan §11.5).

    Returns whether telemetry export is enabled. In v1 this is a config-gated no-op: the live
    OpenTelemetry/Application Insights exporter is wired with the Azure deploy (Phase 14), and
    Container Apps streams the stdout JSON logs to Log Analytics regardless, so the structured
    logs ARE the telemetry locally and in v1 prod. Disabled telemetry never touches the network.
    """
    logger = get_logger(APP_LOGGER_NAME)
    if not settings.telemetry_enabled:
        logger.debug("telemetry.disabled")
        return False
    logger.info("telemetry.enabled", service=settings.telemetry_service_name)
    return True


def log_security_event(event: str, *, request_id: str | None = None, **fields: Any) -> None:
    """Emit a PHI-free security event at WARNING (plan §11.3/§11.7).

    `event` is one of `SECURITY_EVENTS`. Only safe fields (reason, role, route, counts) are
    recorded — never a token, credential, account, or any input value. The request correlation id
    rides the structlog contextvars when inside a request; pass `request_id` for out-of-request
    events. Security events are emitted here AND persisted as `audit_logs` rows by the caller when
    a tenant-scoped session is available (durable record, plan §11.7).
    """
    payload: dict[str, Any] = {key: value for key, value in fields.items() if value is not None}
    if request_id is not None:
        payload["request_id"] = request_id
    get_logger(SECURITY_LOGGER_NAME).warning(event, **payload)


def log_llm_call(  # noqa: PLR0913 - the §11.3 LLM-call cost/usage field set (all keyword-only).
    *,
    model: str,
    prompt_version: str,
    prompt_hash: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    cost_usd: Decimal | float,
    fallback_count: int = 0,
    cached: bool = False,
    latency_ms: float | None = None,
    model_version: str | None = None,
    run_id: str | None = None,
    agency_id: str | None = None,
    agent: str | None = None,
    attempt: int | None = None,
) -> None:
    """Emit a PHI-free LLM-call cost/usage event for cost dashboards (plan §7.4/§11.3).

    Records ONLY model + prompt provenance (version + hash, never the prompt text), token counts,
    estimated USD cost, fallback hops, and cache hits — never prompt/response content or any raw
    input (the full masked SAR lives in `sar_drafts` under tenant scope, not the app log). `run_id`
    / `agency_id` correlate background work; `agent` / `attempt` identify bounded graph calls.
    """
    fields: dict[str, Any] = {
        "model": model,
        "prompt_version": prompt_version,
        "prompt_hash": prompt_hash,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cost_usd": str(cost_usd),
        "fallback_count": fallback_count,
        "cached": cached,
    }
    if latency_ms is not None:
        fields["latency_ms"] = round(latency_ms, 2)
    if model_version is not None:
        fields["model_version"] = model_version
    if run_id is not None:
        fields["run_id"] = run_id
    if agency_id is not None:
        fields["agency_id"] = agency_id
    if agent is not None:
        fields["agent"] = agent
    if attempt is not None:
        fields["attempt"] = attempt
    get_logger(LLM_LOGGER_NAME).info("llm.call", **fields)

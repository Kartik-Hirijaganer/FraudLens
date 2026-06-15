"""Phase 12 observability unit tests (plan §11.3/§11.5/§11.7): the telemetry init switch, the
PHI-free security + LLM-cost event helpers, and the access-log identity binding. Every event is
captured through the real structlog JSON pipeline (the same one prod uses), so the assertions cover
the emitted fields AND that nothing sensitive leaks (the redaction processor runs too).
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from decimal import Decimal

import structlog

from fraudlens_backend.middleware.logging import (
    APP_LOGGER_NAME,
    bind_identity,
    configure_logging,
    get_logger,
)
from fraudlens_backend.settings import AppSettings
from fraudlens_backend.telemetry import (
    SECURITY_EVENTS,
    init_telemetry,
    log_llm_call,
    log_security_event,
)


def _capture(level: str = "INFO") -> io.StringIO:
    """Reconfigure the structlog JSON pipeline onto an in-memory buffer and return it."""
    buf = io.StringIO()
    logger = configure_logging(level, json_logs=True)
    logger.handlers[0].stream = buf  # type: ignore[attr-defined]
    structlog.contextvars.clear_contextvars()
    return buf


def _last_record(buf: io.StringIO) -> dict:
    """Parse the (single) JSON log line captured in the buffer."""
    return json.loads(buf.getvalue().strip())


def test_init_telemetry_off_by_default(make_settings: Callable[..., AppSettings]) -> None:
    assert init_telemetry(make_settings()) is False


def test_init_telemetry_enabled_logs_and_returns_true(
    make_settings: Callable[..., AppSettings],
) -> None:
    buf = _capture()
    enabled = init_telemetry(make_settings(telemetry_enabled=True, telemetry_service_name="svc-x"))
    assert enabled is True
    record = _last_record(buf)
    assert record["event"] == "telemetry.enabled"
    assert record["service"] == "svc-x"


def test_log_security_event_emits_phi_free_warning_with_contextvars() -> None:
    buf = _capture()
    structlog.contextvars.bind_contextvars(request_id="req-1")
    try:
        log_security_event("auth_fail", reason="missing_token", role="analyst", dropped=None)
    finally:
        structlog.contextvars.clear_contextvars()
    record = _last_record(buf)
    assert record["event"] == "auth_fail"
    assert record["level"] == "warning"
    assert record["reason"] == "missing_token"
    assert record["role"] == "analyst"
    assert record["request_id"] == "req-1"  # correlated via the gateway-bound contextvar
    assert "dropped" not in record  # None-valued fields are omitted
    assert "auth_fail" in SECURITY_EVENTS


def test_log_security_event_accepts_explicit_request_id() -> None:
    buf = _capture()
    log_security_event("tenant_mismatch", request_id="req-out-of-band")
    record = _last_record(buf)
    assert record["event"] == "tenant_mismatch"
    assert record["request_id"] == "req-out-of-band"


def test_log_llm_call_records_cost_and_provenance_without_content() -> None:
    buf = _capture()
    log_llm_call(
        model="claude-haiku",
        prompt_version="v1",
        prompt_hash="abc123",
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        cost_usd=Decimal("0.000123"),
        fallback_count=1,
        cached=False,
        latency_ms=42.5,
        model_version="v0-fixture",
        run_id="run-1",
        agency_id="ag-1",
    )
    record = _last_record(buf)
    assert record["event"] == "llm.call"
    assert record["model"] == "claude-haiku"
    assert record["prompt_hash"] == "abc123"
    assert record["cost_usd"] == "0.000123"  # exact string NUMERIC, no float drift
    assert record["total_tokens"] == 30
    assert record["fallback_count"] == 1
    assert record["latency_ms"] == 42.5
    assert record["model_version"] == "v0-fixture"
    assert record["run_id"] == "run-1"
    assert record["agency_id"] == "ag-1"


def test_log_llm_call_omits_absent_optional_fields() -> None:
    buf = _capture()
    log_llm_call(
        model="mock",
        prompt_version="v1",
        prompt_hash="h",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost_usd=0.0,
    )
    record = _last_record(buf)
    assert record["event"] == "llm.call"
    assert record["cached"] is False
    assert "latency_ms" not in record
    assert "model_version" not in record
    assert "run_id" not in record


def test_bind_identity_enriches_subsequent_logs() -> None:
    buf = _capture()
    structlog.contextvars.bind_contextvars(request_id="req-3")
    bind_identity(agency_id="ag-7", user_id="user-7")
    try:
        get_logger(APP_LOGGER_NAME).info("request")
    finally:
        structlog.contextvars.clear_contextvars()
    record = _last_record(buf)
    assert record["agency_id"] == "ag-7"
    assert record["user_id"] == "user-7"


def test_bind_identity_skips_none_and_is_noop_when_empty() -> None:
    buf = _capture()
    bind_identity(agency_id="ag-7")  # user_id None → skipped
    bind_identity()  # all None → binds nothing
    try:
        get_logger(APP_LOGGER_NAME).info("request")
    finally:
        structlog.contextvars.clear_contextvars()
    record = _last_record(buf)
    assert record["agency_id"] == "ag-7"
    assert "user_id" not in record

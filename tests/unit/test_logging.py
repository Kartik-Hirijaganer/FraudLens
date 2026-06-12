"""Unit tests for the structlog pipeline: PHI scrubbing + key-denylist redaction."""

from __future__ import annotations

import io
import json
import logging

import structlog

from fraudlens_backend.middleware.logging import (
    APP_LOGGER_NAME,
    configure_logging,
    get_logger,
    redact_processor,
    scrub_text,
)


def test_scrub_text_redacts_ssn_email_and_card() -> None:
    assert scrub_text("ssn 123-45-6789 here") == "ssn [REDACTED] here"
    assert "[REDACTED]" in scrub_text("mail alice@example.com")
    assert "[REDACTED]" in scrub_text("card 4111111111111111")
    assert scrub_text("nothing sensitive") == "nothing sensitive"


def test_redact_processor_masks_denied_keys_and_scrubs_values() -> None:
    event = {
        "event": "ping ssn 123-45-6789",
        "token": "supersecret",
        "authorization": "Bearer x",
        "signing_key": "abc",
        "database_url": "postgresql+asyncpg://u:p@host/db",
        "origin_account": "12345678",
        "note": "email bob@example.com",
        "count": 7,
    }
    out = redact_processor(None, "info", event)
    assert out["token"] == "[REDACTED]"
    assert out["authorization"] == "[REDACTED]"
    assert out["signing_key"] == "[REDACTED]"  # *_key rule
    assert out["database_url"] == "[REDACTED]"
    assert out["origin_account"] == "[REDACTED]"
    assert "[REDACTED]" in out["event"]  # PHI scrubbed in the message
    assert "[REDACTED]" in out["note"]  # PHI scrubbed in a value
    assert out["count"] == 7  # non-string, non-secret untouched


def test_redact_processor_scrubs_nested_structures() -> None:
    out = redact_processor(
        None,
        "info",
        {"event": "x", "ctx": {"password": "p", "items": ["ssn 123-45-6789"]}},
    )
    assert out["ctx"]["password"] == "[REDACTED]"
    assert out["ctx"]["items"] == ["ssn [REDACTED]"]  # PHI substring scrubbed in place


def test_configure_logging_is_idempotent_and_defaults_unknown_level() -> None:
    logger = configure_logging("BOGUS-LEVEL")
    assert logger.level == logging.INFO
    again = configure_logging("DEBUG")
    assert again.level == logging.DEBUG
    assert len(again.handlers) == 1  # handler not duplicated across calls


def test_json_logger_emits_redacted_line_with_contextvars() -> None:
    buf = io.StringIO()
    logger = configure_logging("INFO", json_logs=True)
    logger.handlers[0].stream = buf  # type: ignore[attr-defined]
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-9")
    try:
        get_logger(APP_LOGGER_NAME).info("hello", token="leakme", ssn="123-45-6789")
    finally:
        structlog.contextvars.clear_contextvars()
    record = json.loads(buf.getvalue().strip())
    assert record["request_id"] == "req-9"
    assert record["level"] == "info"
    assert record["token"] == "[REDACTED]"
    assert record["ssn"] == "[REDACTED]"
    assert "leakme" not in buf.getvalue()


def test_console_renderer_redacts_and_handles_exceptions() -> None:
    buf = io.StringIO()
    logger = configure_logging("INFO", json_logs=False)
    logger.handlers[0].stream = buf  # type: ignore[attr-defined]
    try:
        raise ValueError("leak ssn 123-45-6789")
    except ValueError as exc:
        logging.getLogger(APP_LOGGER_NAME).error("boom", exc_info=exc)
    out = buf.getvalue()
    assert "boom" in out
    assert "123-45-6789" not in out  # PHI scrubbed even inside the rendered traceback


def test_get_logger_without_name_returns_bound_logger() -> None:
    configure_logging("INFO")
    assert get_logger() is not None

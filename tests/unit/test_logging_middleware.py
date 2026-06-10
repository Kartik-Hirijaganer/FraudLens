"""Unit tests for PHI scrubbing, JSON formatting, and the request middleware."""

from __future__ import annotations

import logging
import sys

from fraudlens_backend.middleware.logging import (
    JsonLogFormatter,
    PHIScrubFilter,
    RequestContextMiddleware,
    configure_logging,
)


def test_scrub_redacts_ssn_email_and_card() -> None:
    assert PHIScrubFilter.scrub("ssn 123-45-6789 here") == "ssn [REDACTED] here"
    assert "[REDACTED]" in PHIScrubFilter.scrub("mail alice@example.com")
    assert "[REDACTED]" in PHIScrubFilter.scrub("card 4111111111111111")
    assert PHIScrubFilter.scrub("nothing sensitive") == "nothing sensitive"


def test_filter_mutates_message_and_string_extras_only() -> None:
    record = logging.LogRecord("n", logging.INFO, __file__, 1, "ssn 123-45-6789", (), None)
    record.path = "user alice@example.com"
    record.count = 7  # non-str extra must be left untouched
    PHIScrubFilter().filter(record)
    assert "123-45-6789" not in record.getMessage()
    assert "alice@example.com" not in record.path
    assert record.count == 7


def test_json_formatter_includes_extras_and_exception() -> None:
    formatter = JsonLogFormatter()
    record = logging.LogRecord("n", logging.INFO, __file__, 1, "hi", (), None)
    record.request_id = "abc"
    rendered = formatter.format(record)
    assert '"message": "hi"' in rendered
    assert '"request_id": "abc"' in rendered

    try:
        raise ValueError("boom")
    except ValueError:
        err_record = logging.LogRecord("n", logging.ERROR, __file__, 1, "oops", (), sys.exc_info())
    assert '"error"' in formatter.format(err_record)


def test_configure_logging_is_idempotent_and_defaults_unknown_level() -> None:
    logger = configure_logging("BOGUS-LEVEL")
    assert logger.level == logging.INFO
    again = configure_logging("DEBUG")
    assert again.level == logging.DEBUG
    assert len(again.handlers) == 1  # not duplicated across calls


async def test_middleware_passes_through_non_http_scope() -> None:
    seen: dict[str, object] = {}

    async def downstream(scope: dict, receive: object, send: object) -> None:
        seen["type"] = scope["type"]

    async def receive() -> dict:
        return {"type": "lifespan.startup"}

    async def send(message: dict) -> None:
        return None

    middleware = RequestContextMiddleware(downstream, request_id_header="X-Request-Id")
    await middleware({"type": "lifespan"}, receive, send)
    assert seen["type"] == "lifespan"

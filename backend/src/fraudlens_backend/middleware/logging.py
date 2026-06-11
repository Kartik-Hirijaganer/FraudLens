"""Summary: Structured (JSON) request logging with a PHI-scrubbing filter and a
per-request correlation id. RequestContextMiddleware assigns/propagates a request
id (echoed in a response header and the error envelope), times the request, and
emits one structured access log line per request — logging only method, route
path, status, and duration (never the query string or body, which may carry PHI).
PHIScrubFilter is a defense-in-depth net that redacts SSN / email / card-like
patterns from any log message before it is emitted.

Key classes:
- PHIScrubFilter: logging.Filter that redacts PHI-like substrings.
- JsonLogFormatter: logging.Formatter that renders records as one-line JSON.
- RequestContextMiddleware: assigns the request id and logs each request.

Key functions:
- configure_logging: install the JSON handler + PHI filter on the app logger.

Notes:
- The access log records request.url.path (route path only) — never the full URL
  with query parameters — to keep PHI out of logs (FraudLens governance).
"""

from __future__ import annotations

import json
import logging
import re
import time
from uuid import uuid4

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

ACCESS_LOGGER_NAME = "fraudlens.access"
APP_LOGGER_NAME = "fraudlens"

_RESERVED_RECORD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class PHIScrubFilter(logging.Filter):
    """Redact PHI-like substrings (SSN, email, long card-like digits) from logs."""

    _PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # US SSN
        re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),  # email
        re.compile(r"\b\d{13,19}\b"),  # card / long account numbers
    )
    _REDACTION = "[REDACTED]"

    @classmethod
    def scrub(cls, text: str) -> str:
        """Return text with every PHI-like pattern replaced by [REDACTED]."""
        for pattern in cls._PATTERNS:
            text = pattern.sub(cls._REDACTION, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        """Scrub the rendered message and string extras; never drop the record."""
        rendered = record.getMessage()
        scrubbed = self.scrub(rendered)
        if scrubbed != rendered:
            record.msg = scrubbed
            record.args = ()
        for key, value in list(record.__dict__.items()):
            if key not in _RESERVED_RECORD_ATTRS and isinstance(value, str):
                record.__dict__[key] = self.scrub(value)
        return True


class JsonLogFormatter(logging.Formatter):
    """Render a LogRecord as a single deterministic JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize level, logger, message, and any structured extras to JSON."""
        payload: dict[str, object] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=True)


def configure_logging(log_level: str) -> logging.Logger:
    """Install a JSON stream handler with the PHI filter on the app logger (idempotent)."""
    level = logging.getLevelNamesMapping().get(log_level.upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(PHIScrubFilter())
    logger = logging.getLogger(APP_LOGGER_NAME)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


class RequestContextMiddleware:
    """Pure-ASGI middleware: assign a request id, time the request, log it."""

    def __init__(self, app: ASGIApp, *, request_id_header: str) -> None:
        """Store the wrapped app and the header used to carry the correlation id."""
        self._app = app
        self._header = request_id_header
        self._logger = logging.getLogger(ACCESS_LOGGER_NAME)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Inject the request id into scope state, time the call, emit one log line."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope)
        request_id = request.headers.get(self._header) or uuid4().hex
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id
        start = time.perf_counter()
        status_holder = {"status": 500}
        header_bytes = self._header.encode("latin-1")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message["status"])
                headers = list(message.get("headers", []))
                headers.append((header_bytes, request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            self._logger.info(
                "request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": status_holder["status"],
                    "duration_ms": duration_ms,
                },
            )

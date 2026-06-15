"""Summary: structlog-based structured logging with a PHI/secret redaction
processor (plan §11.1). configure_logging wires structlog onto the stdlib logging
backbone via ProcessorFormatter, so BOTH structlog loggers and any plain stdlib
logger render through the SAME pipeline: contextvar merge (requestId/agencyId/userId)
-> log level -> ISO timestamp -> exception formatting -> redaction -> JSON (prod) or a
console renderer (dev). The redaction step runs LAST, after exception formatting, so
denylisted keys are masked and PHI-shaped substrings (SSN/email/long card-or-account
numbers) are scrubbed from messages, structured values, AND rendered tracebacks before
anything is emitted. The gateway binds the per-request correlation id into contextvars,
so every line in a request is correlated without threading the id through call sites.

Key classes:
- (none)

Key functions:
- scrub_text: redact PHI-like substrings (SSN, email, long card/account digits).
- redact_processor: structlog processor masking denylisted keys + scrubbing values.
- configure_logging: install the structlog + stdlib ProcessorFormatter pipeline.
- get_logger: return a bound structlog logger for the given (optional) name.
- bind_identity: bind the verified agency_id/user_id into the structlog contextvars (§11.4).

Notes:
- Access logs record method, route path, status, and duration only — never the query
  string or body (which may carry PHI) — matching the FraudLens no-PHI-in-logs rule.
- The key denylist (plan §11.1) covers authorization/password/secret/token, any *_key,
  database_url, and the masked-account fields; matched keys are replaced with the
  redaction marker rather than emitting their value.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any, cast

import structlog
from structlog.contextvars import bind_contextvars
from structlog.typing import EventDict, WrappedLogger

APP_LOGGER_NAME = "fraudlens"
ACCESS_LOGGER_NAME = "fraudlens.access"

_REDACTION = "[REDACTED]"

# PHI-shaped substrings scrubbed from every rendered value (defense-in-depth net).
_PHI_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # US SSN
    re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),  # email
    re.compile(r"\b\d{13,19}\b"),  # card / long account numbers
)

# Keys whose VALUE must never be logged (plan §11.1). Exact names plus rules below.
_DENY_EXACT: frozenset[str] = frozenset(
    {"authorization", "password", "secret", "token", "database_url"}
    | {"origin_account", "dest_account"}
)


def scrub_text(text: str) -> str:
    """Return text with every PHI-like pattern replaced by the redaction marker."""
    for pattern in _PHI_PATTERNS:
        text = pattern.sub(_REDACTION, text)
    return text


def _is_denied_key(key: str) -> bool:
    """True when a log field's key names a secret/credential that must be masked.

    The `token` rule matches credential tokens (`token`, `access_token`, `id_token`, …) but NOT a
    token COUNT (`input_tokens`/`output_tokens`/`total_tokens`), which §11.3 logs for LLM cost — a
    credential is singular, a count is the plural `*tokens`, so the plural is allowed through.
    """
    low = key.lower()
    return (
        low in _DENY_EXACT
        or low.endswith("_key")
        or "secret" in low
        or "password" in low
        or ("token" in low and not low.endswith("tokens"))
    )


def _scrub_value(value: Any) -> Any:
    """Recursively scrub strings and mask denied keys inside nested values."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, Mapping):
        return {
            key: (_REDACTION if _is_denied_key(str(key)) else _scrub_value(val))
            for key, val in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub_value(item) for item in value]
    return value


def redact_processor(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """Mask denylisted keys and scrub PHI from every value in the event dict."""
    return {
        key: (_REDACTION if _is_denied_key(str(key)) else _scrub_value(value))
        for key, value in event_dict.items()
    }


def configure_logging(log_level: str, *, json_logs: bool = True) -> logging.Logger:
    """Install the structlog + stdlib ProcessorFormatter pipeline (idempotent).

    Returns the configured `fraudlens` logger; the `fraudlens.access` child logger
    propagates to the same handler. Renders JSON when json_logs is set (prod/staging)
    and a plain console format otherwise (dev). Unknown level names fall back to INFO.
    """
    level = logging.getLevelNamesMapping().get(log_level.upper(), logging.INFO)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    renderer: Any = (
        structlog.processors.JSONRenderer(sort_keys=True)
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    # Redaction runs LAST so the formatted exception string is scrubbed too.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            redact_processor,
            renderer,
        ],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger = logging.getLogger(APP_LOGGER_NAME)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger (optionally named) using the configured pipeline."""
    logger = structlog.get_logger(name) if name else structlog.get_logger()
    return cast(structlog.stdlib.BoundLogger, logger)


def bind_identity(*, agency_id: str | None = None, user_id: str | None = None) -> None:
    """Bind the verified tenant/user identity into the structlog contextvars (plan §11.4).

    The gateway binds the per-request `request_id`; once a route resolves its tenant, this adds
    `agency_id` (and `user_id` when the token carries a subject) so the gateway's access-log line
    and every subsequent record in the request are correlated to the tenant. Both are tenant/user
    ids — never PHI — so they are not on the redaction denylist. None values are skipped so a
    partial identity (e.g. a subject-less token) never overwrites a bound value with a blank.
    """
    fields = {
        key: value
        for key, value in (("agency_id", agency_id), ("user_id", user_id))
        if value is not None
    }
    if fields:
        bind_contextvars(**fields)

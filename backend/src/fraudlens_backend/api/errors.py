"""Summary: Exception handlers that render every error as the FraudLens envelope
{code, message, details, requestId} — never a raw stack trace, exception class
name, or framework default body. The handlers cover the surface: catalog errors
(AppError → the stable business code + HTTP status from models/errors.py), canonical
schema errors (fraudlens_core.SchemaValidationError → 422 with a field/reason detail),
HTTP errors (from raised HTTPException, including auth 401/403), request-validation
errors (field/message pairs only, never echoed input values), and a catch-all for
unhandled exceptions (logged server-side with stack info, but a generic 500 body).

Key classes:
- (none)

Key functions:
- register_exception_handlers: install every error-envelope handler on the FastAPI app.

Notes:
- details carries only {field, message} — raw request values are never reflected
  back, so PHI cannot leak through validation errors (FraudLens governance).
- The requestId is read from request.state (set by the gateway middleware).
"""

from __future__ import annotations

from typing import cast

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from fraudlens_backend.middleware.logging import APP_LOGGER_NAME, get_logger
from fraudlens_backend.models.common import ErrorResponse
from fraudlens_backend.models.errors import AppError, get_error_spec
from fraudlens_core import SchemaValidationError

_GENERIC_500_MESSAGE = "An internal error occurred."

_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


def _request_id(request: Request) -> str:
    """Return the correlation id set by the middleware, or 'unknown' if absent."""
    return str(getattr(request.state, "request_id", "unknown"))


def _envelope(
    *,
    code: str,
    message: str,
    status_code: int,
    request: Request,
    details: list[dict[str, str]] | None = None,
) -> JSONResponse:
    """Build a JSONResponse carrying the FraudLens error envelope."""
    body = ErrorResponse(
        code=code,
        message=message,
        details=details,
        request_id=_request_id(request),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(by_alias=True))


async def _app_error_handler(request: Request, exc: Exception) -> Response:
    """Render a raised AppError using its catalog code, status, and fixed message."""
    app_exc = cast(AppError, exc)
    spec = get_error_spec(app_exc.code)
    return _envelope(
        code=spec.code,
        message=spec.message,
        status_code=spec.http_status,
        request=request,
        details=app_exc.details,
    )


async def _schema_validation_handler(request: Request, exc: Exception) -> Response:
    """Render a canonical SchemaValidationError as a 422 with a field/reason detail."""
    schema_exc = cast(SchemaValidationError, exc)
    return _envelope(
        code="validation_error",
        message="Request validation failed.",
        status_code=422,
        request=request,
        details=[{"field": schema_exc.field, "message": schema_exc.reason}],
    )


async def _http_exception_handler(request: Request, exc: Exception) -> Response:
    """Render a raised HTTPException as the FraudLens envelope."""
    http_exc = cast(StarletteHTTPException, exc)
    code = _STATUS_CODES.get(http_exc.status_code, "http_error")
    message = http_exc.detail if isinstance(http_exc.detail, str) else code.replace("_", " ")
    return _envelope(
        code=code,
        message=message,
        status_code=http_exc.status_code,
        request=request,
    )


async def _validation_exception_handler(request: Request, exc: Exception) -> Response:
    """Render request-validation failures as field/message detail pairs (no values)."""
    validation_exc = cast(RequestValidationError, exc)
    details = [
        {
            "field": ".".join(str(part) for part in error.get("loc", ())),
            "message": str(error.get("msg", "invalid")),
        }
        for error in validation_exc.errors()
    ]
    return _envelope(
        code="validation_error",
        message="Request validation failed.",
        status_code=422,
        request=request,
        details=details,
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    """Log the failure server-side and return a generic 500 with no internals leaked."""
    get_logger(APP_LOGGER_NAME).error(
        "unhandled exception",
        request_id=_request_id(request),
        exc_info=exc,
    )
    return _envelope(
        code="internal_error",
        message=_GENERIC_500_MESSAGE,
        status_code=500,
        request=request,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Install the AppError, HTTP, validation, and catch-all handlers on the app."""
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(SchemaValidationError, _schema_validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

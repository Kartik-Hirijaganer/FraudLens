"""Summary: The gateway edge — the single trust boundary in front of the in-process
service modules (plan §4). In v1 the gateway is this FastAPI middleware stack; the
services are in-process modules with clean interfaces, so the full trust boundary is
realized at $0 and splits into internal-ingress service apps later (ADR-004) without
touching the SPA. GatewayMiddleware (pure-ASGI, outermost) issues/propagates the
X-Request-Id, binds it into the structlog contextvars for end-to-end correlation,
applies a fixed-window per-client rate limit (429 envelope), stamps configured
security headers onto every response, runs an optional authN/Z hook seam, and emits
one uniform access-log line. CORS is enforced by Starlette's CORSMiddleware from the
boot-critical allowlist. The routing table is config-driven (config/gateway/routes.yaml)
and loaded at startup; the matched route name is logged for correlation.

Key classes:
- RouteRule: one gateway route (path prefix -> service, role, rate limit).
- GatewayRoutes: the validated routing table loaded from routes.yaml.
- GatewayMiddleware: the ASGI edge (request-id, rate-limit, headers, access log).

Key functions:
- load_gateway_routes: load + validate the routing table (empty when absent).
- install_gateway: add the CORS + gateway middleware stack to a FastAPI app.

Notes:
- AuthN/AuthZ in v1 is enforced per-route by api/deps.py (the in-process edge);
  authn_hook is the seam where edge JWT verification moves when services split. The
  global rate limit is active in v1; per-route limits in routes.yaml activate at the
  split (ADR-004). Ops probes (/healthz, /readyz) and CORS preflight are never throttled.
- The rate limiter is an IN-MEMORY fixed window, so its counters are PER-REPLICA. That
  is correct for the v1 single-replica, scale-to-zero deployment (plan §10.6); a shared
  store (e.g. Redis) is the documented scale-up when running multiple replicas. The
  counter map is pruned past _MAX_TRACKED_CLIENTS so one-off clients can't leak memory.
- The access log records method/route-path/status/duration only — never the query
  string or body — preserving the FraudLens no-PHI-in-logs rule.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field
from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bind_contextvars, clear_contextvars

from fraudlens_backend.middleware.logging import ACCESS_LOGGER_NAME, get_logger
from fraudlens_backend.models.common import ErrorResponse
from fraudlens_backend.settings import AppSettings, find_config_dir

_RATE_LIMIT_EXEMPT_PATHS = frozenset({"/healthz", "/readyz"})
_PREFLIGHT_METHOD = "OPTIONS"
# Soft cap on tracked rate-limit clients; once exceeded, expired counters are pruned so
# the in-memory window map cannot grow without bound from one-off client addresses.
_MAX_TRACKED_CLIENTS = 10_000


class RouteRule(BaseModel):
    """One gateway route: which service handles a path prefix, and its edge policy."""

    model_config = ConfigDict(extra="forbid")

    path_prefix: str = Field(..., description="URL path prefix this rule matches.")
    service: str = Field(..., description="In-process service (v1) / internal DNS name (split).")
    required_role: str | None = Field(
        default=None, description="Role required at the edge (enforced per-route by deps in v1)."
    )
    rate_limit_per_minute: int | None = Field(
        default=None, description="Per-route request budget; active when services split (ADR-004)."
    )


class GatewayRoutes(BaseModel):
    """The validated gateway routing table (config-driven, boot-critical)."""

    model_config = ConfigDict(extra="forbid")

    routes: list[RouteRule] = Field(
        default_factory=list, description="Ordered route rules; longest matching prefix wins."
    )

    def match(self, path: str) -> RouteRule | None:
        """Return the rule whose path_prefix is the longest match for path, else None."""
        best: RouteRule | None = None
        for rule in self.routes:
            if path.startswith(rule.path_prefix) and (
                best is None or len(rule.path_prefix) > len(best.path_prefix)
            ):
                best = rule
        return best


def load_gateway_routes(settings: AppSettings) -> GatewayRoutes:
    """Load + validate the routing table from YAML; an absent file yields empty routes."""
    if settings.gateway_routes_file:
        path: Path | None = Path(settings.gateway_routes_file)
    else:
        candidate = find_config_dir() / "gateway" / "routes.yaml"
        path = candidate if candidate.is_file() else None
    if path is None or not path.is_file():
        return GatewayRoutes()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return GatewayRoutes.model_validate(data)


class GatewayMiddleware:
    """Pure-ASGI gateway edge: request-id, rate-limit, security headers, access log."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: AppSettings,
        routes: GatewayRoutes | None = None,
        authn_hook: Callable[[Request], None] | None = None,
    ) -> None:
        """Capture the wrapped app and the boot-critical edge config from settings."""
        self._app = app
        self._header = settings.request_id_header
        self._security_headers = settings.security_headers
        self._rate_limit_enabled = settings.rate_limit_enabled
        self._rate_limit_requests = settings.rate_limit_requests
        self._rate_limit_window = settings.rate_limit_window_seconds
        self._routes = routes or GatewayRoutes()
        self._authn_hook = authn_hook
        self._logger = get_logger(ACCESS_LOGGER_NAME)
        self._hits: dict[str, tuple[float, int]] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Bind correlation context, enforce edge policy, time + log the request."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope)
        request_id = request.headers.get(self._header) or uuid4().hex
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id
        route = self._routes.match(request.url.path)

        clear_contextvars()
        bind_contextvars(request_id=request_id)
        if self._authn_hook is not None:
            self._authn_hook(request)

        start = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message["status"])
                headers = MutableHeaders(raw=list(message.get("headers", [])))
                headers[self._header] = request_id
                for name, value in self._security_headers.items():
                    headers[name] = value
                message = {**message, "headers": headers.raw}
            await send(message)

        try:
            if self._should_rate_limit(request) and self._is_over_limit(request):
                status_holder["status"] = 429
                await self._send_rate_limited(send_wrapper, request_id)
            else:
                await self._app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            self._logger.info(
                "request",
                method=request.method,
                path=request.url.path,
                status=status_holder["status"],
                duration_ms=duration_ms,
                route=route.service if route else None,
            )
            clear_contextvars()

    def _should_rate_limit(self, request: Request) -> bool:
        """True when the limiter applies (enabled, not a preflight, not an ops probe)."""
        return (
            self._rate_limit_enabled
            and request.method != _PREFLIGHT_METHOD
            and request.url.path not in _RATE_LIMIT_EXEMPT_PATHS
        )

    def _is_over_limit(self, request: Request) -> bool:
        """Increment the client's fixed-window counter; True once it exceeds the budget."""
        key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        if len(self._hits) > _MAX_TRACKED_CLIENTS:
            self._prune_expired(now)
        window_start, count = self._hits.get(key, (now, 0))
        if now - window_start >= self._rate_limit_window:
            window_start, count = now, 0
        count += 1
        self._hits[key] = (window_start, count)
        return count > self._rate_limit_requests

    def _prune_expired(self, now: float) -> None:
        """Drop counters whose window has fully elapsed, bounding in-memory growth."""
        self._hits = {
            key: value
            for key, value in self._hits.items()
            if now - value[0] < self._rate_limit_window
        }

    async def _send_rate_limited(
        self, send_wrapper: Callable[[Message], Awaitable[None]], request_id: str
    ) -> None:
        """Emit a 429 carrying the FraudLens error envelope (reused, never duplicated)."""
        envelope = ErrorResponse(
            code="rate_limited",
            message="Rate limit exceeded.",
            request_id=request_id,
        ).model_dump(by_alias=True)
        body = json.dumps(envelope).encode("utf-8")
        retry_after = str(int(self._rate_limit_window)).encode("latin-1")
        await send_wrapper(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", retry_after),
                ],
            }
        )
        await send_wrapper({"type": "http.response.body", "body": body})


def install_gateway(app: Starlette, settings: AppSettings) -> GatewayRoutes:
    """Add the CORS + gateway edge middleware stack; return the loaded routing table.

    GatewayMiddleware is added last so it is the OUTERMOST layer — it stamps the
    request-id + security headers onto, and logs, even CORS-preflight responses.
    """
    routes = load_gateway_routes(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
        allow_credentials=settings.cors_allow_credentials,
    )
    app.add_middleware(
        GatewayMiddleware,
        settings=settings,
        routes=routes,
    )
    return routes

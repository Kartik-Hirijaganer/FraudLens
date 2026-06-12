"""Gateway edge tests: request-id, security headers, CORS, rate limit, routing table."""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from fraudlens_backend.middleware import gateway as gateway_module
from fraudlens_backend.middleware.gateway import (
    GatewayMiddleware,
    GatewayRoutes,
    RouteRule,
    load_gateway_routes,
)
from fraudlens_backend.settings import AppSettings

SECURITY_HEADERS = {
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "strict-transport-security",
}


def test_request_id_is_issued_and_returned(client_factory: Callable[..., TestClient]) -> None:
    response = client_factory().get("/api/v1/health")
    assert response.status_code == 200
    assert response.headers.get("X-Request-Id")


def test_incoming_request_id_is_propagated(client_factory: Callable[..., TestClient]) -> None:
    response = client_factory().get("/api/v1/health", headers={"X-Request-Id": "rid-42"})
    assert response.headers["X-Request-Id"] == "rid-42"


def test_security_headers_present_on_every_response(
    client_factory: Callable[..., TestClient],
) -> None:
    response = client_factory().get("/api/v1/health")
    assert SECURITY_HEADERS.issubset({key.lower() for key in response.headers})
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_cors_allows_configured_origin(client_factory: Callable[..., TestClient]) -> None:
    # dev.yaml allows http://localhost:5173 as a CORS origin.
    response = client_factory().get("/api/v1/health", headers={"Origin": "http://localhost:5173"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_denies_unconfigured_origin(client_factory: Callable[..., TestClient]) -> None:
    response = client_factory().get("/api/v1/health", headers={"Origin": "http://evil.example"})
    assert response.headers.get("access-control-allow-origin") is None


def test_rate_limit_returns_429_envelope(client_factory: Callable[..., TestClient]) -> None:
    client = client_factory(rate_limit_requests=2, rate_limit_window_seconds=60)
    codes = [client.get("/api/v1/health").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    body = client.get("/api/v1/health").json()
    assert body["code"] == "rate_limited"
    assert body["requestId"]


def test_ops_probes_are_exempt_from_rate_limit(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory(rate_limit_requests=1, rate_limit_window_seconds=60)
    assert [client.get("/healthz").status_code for _ in range(3)] == [200, 200, 200]


def test_rate_limit_can_be_disabled(client_factory: Callable[..., TestClient]) -> None:
    client = client_factory(rate_limit_enabled=False, rate_limit_requests=1)
    assert [client.get("/api/v1/health").status_code for _ in range(3)] == [200, 200, 200]


def test_routes_load_and_longest_prefix_match() -> None:
    routes = load_gateway_routes(AppSettings(environment="dev"))
    assert routes.match("/api/v1/transactions/1").service == "ingestion"
    assert routes.match("/api/v1/config").service == "admin"
    assert routes.match("/api/v1/agencies/acme").service == "tenancy"
    assert routes.match("/nope") is None


def test_routes_absent_file_yields_empty() -> None:
    routes = load_gateway_routes(
        AppSettings(environment="dev", gateway_routes_file="/no/such/routes.yaml")
    )
    assert routes.routes == []


def test_routes_reject_unknown_keys() -> None:
    with pytest.raises(ValueError, match="extra"):
        GatewayRoutes.model_validate({"routes": [{"pathPrefix": "/x", "service": "y"}]})


def test_rate_limiter_resets_after_window() -> None:
    async def _noop(*_args: object) -> None:
        return None

    middleware = GatewayMiddleware(
        _noop,
        settings=AppSettings(
            environment="dev", rate_limit_requests=1, rate_limit_window_seconds=0.01
        ),
    )
    request = Request({"type": "http", "client": ("testhost", 1)})
    assert middleware._is_over_limit(request) is False  # 1st request within budget
    assert middleware._is_over_limit(request) is True  # 2nd exceeds the budget
    time.sleep(0.02)  # window elapses
    assert middleware._is_over_limit(request) is False  # counter reset for the new window


def test_rate_limiter_prunes_expired_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway_module, "_MAX_TRACKED_CLIENTS", 2)

    async def _noop(*_args: object) -> None:
        return None

    middleware = GatewayMiddleware(
        _noop,
        settings=AppSettings(
            environment="dev", rate_limit_requests=100, rate_limit_window_seconds=0.01
        ),
    )
    for host in ("a", "b", "c"):
        middleware._is_over_limit(Request({"type": "http", "client": (host, 1)}))
    assert len(middleware._hits) == 3  # accumulated; cap not yet exceeded at insert time
    time.sleep(0.02)  # every window elapses
    middleware._is_over_limit(Request({"type": "http", "client": ("d", 1)}))
    assert set(middleware._hits) == {"d"}  # expired counters pruned, only the new client


async def test_middleware_passes_through_non_http_scope() -> None:
    seen: dict[str, object] = {}

    async def downstream(scope: dict, receive: object, send: object) -> None:
        seen["type"] = scope["type"]

    async def receive() -> dict:
        return {"type": "lifespan.startup"}

    async def send(_message: dict) -> None:
        return None

    middleware = GatewayMiddleware(downstream, settings=AppSettings(environment="dev"))
    await middleware({"type": "lifespan"}, receive, send)
    assert seen["type"] == "lifespan"


def test_authn_hook_is_invoked() -> None:
    calls: list[str] = []

    async def handler(_request: object) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/x", handler)])
    app.add_middleware(
        GatewayMiddleware,
        settings=AppSettings(environment="dev"),
        routes=GatewayRoutes(routes=[RouteRule(path_prefix="/x", service="svc")]),
        authn_hook=lambda _request: calls.append("hit"),
    )
    TestClient(app, raise_server_exceptions=False).get("/x")
    assert calls == ["hit"]

"""Per-route rate-limit tests (plan §16 Phase 13): the stricter per-route limiter Phase 13 adds
in api/deps.py, layered on the global gateway limiter (covered by test_gateway.py) as
defense-in-depth for the abuse-prone telemetry client-error sink. Covers the sliding-window
counter directly and the 429 envelope end-to-end through the endpoint."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from fraudlens_backend.api.deps import _SlidingWindowLimiter

_TELEMETRY = "/api/v1/telemetry/client-error"


def test_sliding_window_counts_within_budget_then_trips() -> None:
    limiter = _SlidingWindowLimiter(limit=2, window_seconds=10.0)
    assert limiter.over_limit("client-a", now=100.0) is False  # 1st
    assert limiter.over_limit("client-a", now=100.0) is False  # 2nd — at budget
    assert limiter.over_limit("client-a", now=100.0) is True  # 3rd — over budget


def test_sliding_window_resets_once_the_window_elapses() -> None:
    limiter = _SlidingWindowLimiter(limit=1, window_seconds=10.0)
    assert limiter.over_limit("client-a", now=100.0) is False
    assert limiter.over_limit("client-a", now=100.0) is True  # 2nd within window
    assert limiter.over_limit("client-a", now=200.0) is False  # window elapsed → decayed


def test_sliding_window_keys_clients_independently() -> None:
    limiter = _SlidingWindowLimiter(limit=1, window_seconds=10.0)
    assert limiter.over_limit("client-a", now=100.0) is False
    assert limiter.over_limit("client-b", now=100.0) is False  # a different client is not affected


def test_telemetry_sink_429s_past_the_per_route_budget(
    client_factory: Callable[..., TestClient],
) -> None:
    # Disable the global gateway limiter so ONLY the per-route limiter can trip here.
    client = client_factory(
        environment="dev",
        auth_dev_bypass=True,
        rate_limit_enabled=False,
        client_error_rate_limit_requests=3,
    )
    codes = [client.post(_TELEMETRY, json={"message": "boom"}).status_code for _ in range(4)]
    assert codes == [202, 202, 202, 429]


def test_telemetry_429_carries_the_error_envelope(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory(
        environment="dev",
        auth_dev_bypass=True,
        rate_limit_enabled=False,
        client_error_rate_limit_requests=1,
    )
    client.post(_TELEMETRY, json={"message": "first"})
    blocked = client.post(_TELEMETRY, json={"message": "second"})
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["code"] == "rate_limited"
    assert body["requestId"]

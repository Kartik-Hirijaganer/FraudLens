"""Integration tests for the unprefixed ops probes (/healthz, /readyz)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from fraudlens_backend.api.ops import DependencyCheck, get_readiness_probes


def test_healthz_is_ok(client_factory: Callable[..., TestClient]) -> None:
    response = client_factory().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers.get("X-Request-Id")


def test_readyz_is_ready_with_skipped_dependencies(
    client_factory: Callable[..., TestClient],
) -> None:
    response = client_factory().get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert {check["name"] for check in body["checks"]} == {"database", "chromadb", "akeyless"}
    assert all(check["status"] == "skipped" for check in body["checks"])


def test_readyz_is_503_when_a_dependency_is_down(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory()
    client.app.dependency_overrides[get_readiness_probes] = lambda: [
        lambda: DependencyCheck(name="database", status="down", detail="unreachable")
    ]
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"

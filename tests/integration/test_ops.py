"""Integration tests for the unprefixed ops probes (/healthz, /readyz)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from fraudlens_backend.api.ops import DependencyCheck, get_readiness_probes


class _FakeConn:
    """Async-context-manager connection used to stub a reachable database."""

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def execute(self, _statement: object) -> None:
        return None


class _OkEngine:
    """Engine stub whose connect() yields a working connection."""

    def connect(self) -> _FakeConn:
        return _FakeConn()


class _BadEngine:
    """Engine stub whose connect() fails (unreachable database)."""

    def connect(self) -> _FakeConn:
        raise OSError("connection refused")


def _database_check(body: dict) -> dict:
    """Return the 'database' dependency check from a /readyz body."""
    return next(check for check in body["checks"] if check["name"] == "database")


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
    assert {check["name"] for check in body["checks"]} == {"database", "chromadb", "infisical"}
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


def test_readyz_reports_database_ok_when_engine_reachable(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory()
    client.app.state.db_engine = _OkEngine()
    response = client.get("/readyz")
    assert response.status_code == 200
    assert _database_check(response.json())["status"] == "ok"


def test_readyz_reports_database_down_when_engine_unreachable(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory()
    client.app.state.db_engine = _BadEngine()
    response = client.get("/readyz")
    assert response.status_code == 503
    assert _database_check(response.json())["status"] == "down"

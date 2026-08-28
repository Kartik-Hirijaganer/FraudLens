"""Integration tests for the unprefixed ops probes (/healthz, /readyz), including the Phase 6
ChromaDB RAG-index presence check (ok / down-when-required / skipped)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fraudlens_backend.api import ops
from fraudlens_backend.api.ops import DependencyCheck, get_readiness_probes
from fraudlens_ml.rag import HashingEmbedder, RegulationDocument, build_index, chunk_corpus


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


def _check(body: dict, name: str) -> dict:
    """Return a named dependency check from a /readyz body."""
    return next(check for check in body["checks"] if check["name"] == name)


def _build_fixture_index(directory: Path, collection: str) -> Path:
    """Build a tiny ready ChromaDB index at a directory and return it (for the 'ok' probe)."""
    doc = RegulationDocument(
        doc_id="d", title="T", citation="31 CFR 1010.314", source="FinCEN", text="structuring cash"
    )
    build_index(
        chunk_corpus([doc]),
        embedder=HashingEmbedder(),
        persist_dir=directory,
        collection=collection,
    )
    return directory


def test_healthz_is_ok(client_factory: Callable[..., TestClient]) -> None:
    response = client_factory().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers.get("X-Request-Id")


def test_readyz_is_ready_with_skipped_dependencies(
    client_factory: Callable[..., TestClient], tmp_path: Path
) -> None:
    client = client_factory()
    client.app.state.rag_index_dir = tmp_path / "absent"  # hermetic: no built index here
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert {check["name"] for check in body["checks"]} == {
        "database",
        "chromadb",
        "supabaseAuth",
        "infisical",
        "openrouter",
    }
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
    client.app.state.rag_index_dir = None  # isolate the database check
    response = client.get("/readyz")
    assert response.status_code == 200
    assert _check(response.json(), "database")["status"] == "ok"


def test_readyz_reports_database_down_when_engine_unreachable(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory()
    client.app.state.db_engine = _BadEngine()
    response = client.get("/readyz")
    assert response.status_code == 503
    assert _check(response.json(), "database")["status"] == "down"


def test_readyz_reports_chromadb_ok_when_index_present(
    client_factory: Callable[..., TestClient], tmp_path: Path
) -> None:
    client = client_factory()
    client.app.state.rag_index_dir = _build_fixture_index(
        tmp_path / "chroma", client.app.state.settings.rag_collection
    )
    response = client.get("/readyz")
    assert response.status_code == 200
    assert _check(response.json(), "chromadb")["status"] == "ok"


def test_readyz_chromadb_down_when_index_required_but_missing(
    client_factory: Callable[..., TestClient], tmp_path: Path
) -> None:
    client = client_factory(rag_index_required=True)
    client.app.state.rag_index_dir = tmp_path / "absent"  # required but never built
    response = client.get("/readyz")
    assert response.status_code == 503
    assert _check(response.json(), "chromadb")["status"] == "down"


def test_readyz_chromadb_down_when_required_embedding_space_mismatches(
    client_factory: Callable[..., TestClient], tmp_path: Path
) -> None:
    index_dir = tmp_path / "hashing-index"
    client = client_factory(
        rag_embedding_mode="live",
        rag_index_required=True,
        rag_index_dir=str(index_dir),
    )
    client.app.state.rag_index_dir = _build_fixture_index(
        index_dir, client.app.state.settings.rag_collection
    )
    response = client.get("/readyz")
    assert response.status_code == 503
    check = _check(response.json(), "chromadb")
    assert check["status"] == "down"
    assert check["detail"] == "index mismatch"


def test_readyz_chromadb_skipped_when_index_dir_unset(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory()
    client.app.state.rag_index_dir = None
    assert _check(client.get("/readyz").json(), "chromadb")["status"] == "skipped"


def test_readyz_reports_supabase_auth_ok_when_configured_and_reachable(
    client_factory: Callable[..., TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def ok(_url: str, _timeout: float) -> int:
        return 200

    monkeypatch.setattr(ops, "_fetch_status", ok)
    client = client_factory(auth_jwks_url="https://supabase.example.test/auth/v1/jwks")
    response = client.get("/readyz")
    assert response.status_code == 200
    assert _check(response.json(), "supabaseAuth")["status"] == "ok"


def test_readyz_reports_supabase_auth_down_when_configured_but_unreachable(
    client_factory: Callable[..., TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def down(_url: str, _timeout: float) -> int:
        raise OSError("unreachable")

    monkeypatch.setattr(ops, "_fetch_status", down)
    client = client_factory(auth_jwks_url="https://supabase.example.test/auth/v1/jwks")
    response = client.get("/readyz")
    assert response.status_code == 503
    assert _check(response.json(), "supabaseAuth")["status"] == "down"


def test_readyz_live_profile_rejects_skipped_dependencies(
    client_factory: Callable[..., TestClient],
) -> None:
    """A live LLM profile is not ready when any mandatory dependency is skipped."""
    response = client_factory(llm_mode="live").get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_readyz_live_profile_requires_all_dependencies_ok(
    client_factory: Callable[..., TestClient],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A fully configured live profile reports ready only when all five probes pass."""

    async def ok(_url: str, _timeout: float, **_kwargs: object) -> int:
        return 200

    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-test-value")
    monkeypatch.setattr(ops, "_fetch_status", ok)
    client = client_factory(
        llm_mode="live",
        auth_jwks_url="https://supabase.example.test/auth/v1/jwks",
    )
    client.app.state.db_engine = _OkEngine()
    client.app.state.rag_index_dir = _build_fixture_index(
        tmp_path / "live-chroma", client.app.state.settings.rag_collection
    )
    client.app.state.infisical_readiness_probe = lambda: DependencyCheck(
        name="infisical", status="ok"
    )

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert all(check["status"] == "ok" for check in response.json()["checks"])


def test_resolve_index_dir_keeps_absolute_paths(
    client_factory: Callable[..., TestClient], tmp_path: Path
) -> None:
    absolute = tmp_path / "abs-index"
    client = client_factory(rag_index_dir=str(absolute))
    assert client.app.state.rag_index_dir == absolute

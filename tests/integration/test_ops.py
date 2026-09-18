"""Integration tests for the unprefixed ops probes (/healthz, /readyz), including the Phase 6
ChromaDB RAG-index presence check (ok / down-when-required / skipped) and the config-driven
Infisical secret-delivery check that makes live-mode readiness reachable."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ops_probes import BadEngine, OkEngine, build_fixture_index, readiness_check

from fraudlens_backend.api import ops
from fraudlens_backend.api.ops import DependencyCheck, get_readiness_probes


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
        "llmProvider",
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
    client.app.state.db_engine = OkEngine()
    client.app.state.rag_index_dir = None  # isolate the database check
    response = client.get("/readyz")
    assert response.status_code == 200
    assert readiness_check(response.json(), "database")["status"] == "ok"


def test_readyz_reports_database_down_when_engine_unreachable(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory()
    client.app.state.db_engine = BadEngine()
    response = client.get("/readyz")
    assert response.status_code == 503
    assert readiness_check(response.json(), "database")["status"] == "down"


def test_readyz_reports_chromadb_ok_when_index_present(
    client_factory: Callable[..., TestClient], tmp_path: Path
) -> None:
    client = client_factory()
    client.app.state.rag_index_dir = build_fixture_index(
        tmp_path / "chroma", client.app.state.settings.rag_collection
    )
    response = client.get("/readyz")
    assert response.status_code == 200
    assert readiness_check(response.json(), "chromadb")["status"] == "ok"


def test_readyz_chromadb_down_when_index_required_but_missing(
    client_factory: Callable[..., TestClient], tmp_path: Path
) -> None:
    client = client_factory(rag_index_required=True)
    client.app.state.rag_index_dir = tmp_path / "absent"  # required but never built
    response = client.get("/readyz")
    assert response.status_code == 503
    assert readiness_check(response.json(), "chromadb")["status"] == "down"


def test_readyz_chromadb_down_when_required_embedding_space_mismatches(
    client_factory: Callable[..., TestClient], tmp_path: Path
) -> None:
    index_dir = tmp_path / "hashing-index"
    client = client_factory(
        rag_embedding_mode="live",
        rag_index_required=True,
        rag_index_dir=str(index_dir),
    )
    client.app.state.rag_index_dir = build_fixture_index(
        index_dir, client.app.state.settings.rag_collection
    )
    response = client.get("/readyz")
    assert response.status_code == 503
    check = readiness_check(response.json(), "chromadb")
    assert check["status"] == "down"
    assert check["detail"] == "index mismatch"


def test_readyz_chromadb_skipped_when_index_dir_unset(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory()
    client.app.state.rag_index_dir = None
    assert readiness_check(client.get("/readyz").json(), "chromadb")["status"] == "skipped"


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
    assert readiness_check(response.json(), "supabaseAuth")["status"] == "ok"


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
    assert readiness_check(response.json(), "supabaseAuth")["status"] == "down"


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
    client.app.state.db_engine = OkEngine()
    client.app.state.rag_index_dir = build_fixture_index(
        tmp_path / "live-chroma", client.app.state.settings.rag_collection
    )
    client.app.state.infisical_readiness_probe = lambda: DependencyCheck(
        name="infisical", status="ok"
    )

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert all(check["status"] == "ok" for check in response.json()["checks"])
    # The detail names the last STAGE probed: a cascade has several, a single route has one.
    assert readiness_check(response.json(), "llmProvider")["detail"] == "primary"


def test_readyz_infisical_skipped_when_no_delivery_declared(
    client_factory: Callable[..., TestClient],
) -> None:
    """With no declared delivery mechanism the Infisical check stays informational."""
    check = readiness_check(client_factory().get("/readyz").json(), "infisical")
    assert check["status"] == "skipped"
    assert check["detail"] == "not configured"


def test_readyz_infisical_ok_when_injected_secrets_present(
    client_factory: Callable[..., TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declared injection whose env names are all populated reports ok."""
    monkeypatch.setenv("FRAUDLENS_TEST_INJECTED_A", "synthetic-test-value")
    monkeypatch.setenv("FRAUDLENS_TEST_INJECTED_B", "synthetic-test-value")
    client = client_factory(
        infisical_secrets_delivery="externally_injected",
        infisical_required_env_keys=["FRAUDLENS_TEST_INJECTED_A", "FRAUDLENS_TEST_INJECTED_B"],
    )
    response = client.get("/readyz")
    assert response.status_code == 200
    check = readiness_check(response.json(), "infisical")
    assert check["status"] == "ok"
    assert check["detail"] == "2 injected secret(s) present"


def test_readyz_infisical_down_when_an_injected_secret_is_missing(
    client_factory: Callable[..., TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed secret sync (name never injected) fails readiness closed with 503."""
    monkeypatch.delenv("FRAUDLENS_TEST_INJECTED_A", raising=False)
    client = client_factory(
        infisical_secrets_delivery="externally_injected",
        infisical_required_env_keys=["FRAUDLENS_TEST_INJECTED_A"],
    )
    response = client.get("/readyz")
    assert response.status_code == 503
    check = readiness_check(response.json(), "infisical")
    assert check["status"] == "down"
    assert check["detail"] == "1 injected secret(s) missing"
    # The response is unauthenticated: it must never disclose the secret inventory.
    assert "FRAUDLENS_TEST_INJECTED_A" not in response.text


def test_readyz_infisical_down_when_an_injected_secret_is_blank(
    client_factory: Callable[..., TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A present-but-blank value counts as missing, not as a delivered secret."""
    monkeypatch.setenv("FRAUDLENS_TEST_INJECTED_A", "   ")
    client = client_factory(
        infisical_secrets_delivery="externally_injected",
        infisical_required_env_keys=["FRAUDLENS_TEST_INJECTED_A"],
    )
    response = client.get("/readyz")
    assert response.status_code == 503
    assert readiness_check(response.json(), "infisical")["status"] == "down"


def test_readyz_live_profile_is_ready_without_an_app_state_infisical_probe(
    client_factory: Callable[..., TestClient],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Regression: live mode must reach 200 from config alone.

    Production code registers no `app.state.infisical_readiness_probe`, so before the
    config-driven delivery check the `infisical` probe was permanently "skipped" and a
    live profile (config/prod.yaml) could never satisfy the all-checks-ok gate — the
    Azure Container Apps and Kubernetes readiness probes would have failed forever.
    """

    async def ok(_url: str, _timeout: float, **_kwargs: object) -> int:
        return 200

    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-test-value")
    monkeypatch.setattr(ops, "_fetch_status", ok)
    client = client_factory(
        llm_mode="live",
        auth_jwks_url="https://supabase.example.test/auth/v1/jwks",
        infisical_secrets_delivery="externally_injected",
        infisical_required_env_keys=["OPENROUTER_API_KEY"],
    )
    client.app.state.db_engine = OkEngine()
    client.app.state.rag_index_dir = build_fixture_index(
        tmp_path / "live-chroma", client.app.state.settings.rag_collection
    )
    assert not hasattr(client.app.state, "infisical_readiness_probe")

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert readiness_check(response.json(), "infisical")["status"] == "ok"


def test_readyz_live_profile_is_503_when_the_secret_injection_failed(
    client_factory: Callable[..., TestClient],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The fix stays fail-closed: an otherwise-healthy live profile is not ready."""

    async def ok(_url: str, _timeout: float, **_kwargs: object) -> int:
        return 200

    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-test-value")
    monkeypatch.delenv("FRAUDLENS_TEST_INJECTED_A", raising=False)
    monkeypatch.setattr(ops, "_fetch_status", ok)
    client = client_factory(
        llm_mode="live",
        auth_jwks_url="https://supabase.example.test/auth/v1/jwks",
        infisical_secrets_delivery="externally_injected",
        infisical_required_env_keys=["FRAUDLENS_TEST_INJECTED_A"],
    )
    client.app.state.db_engine = OkEngine()
    client.app.state.rag_index_dir = build_fixture_index(
        tmp_path / "live-chroma-degraded", client.app.state.settings.rag_collection
    )

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert readiness_check(response.json(), "infisical")["status"] == "down"


def test_resolve_index_dir_keeps_absolute_paths(
    client_factory: Callable[..., TestClient], tmp_path: Path
) -> None:
    absolute = tmp_path / "abs-index"
    client = client_factory(rag_index_dir=str(absolute))
    assert client.app.state.rag_index_dir == absolute


class _FakeTokenProvider:
    """Record every audience a readiness probe asks for, and optionally fail on demand."""

    def __init__(self, recorded: list[str], *, fail: bool) -> None:
        self._recorded = recorded
        self._fail = fail

    def token(self, resource: str) -> str:
        self._recorded.append(resource)
        if self._fail:
            raise RuntimeError("token endpoint unreachable")
        return "token"


@pytest.fixture
def fake_token_provider(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[str]]:
    """Install a recording token provider and return the list of audiences it is asked for."""

    def _install(*, fail: bool = False) -> list[str]:
        recorded: list[str] = []
        monkeypatch.setattr(
            ops,
            "ManagedIdentityTokenProvider",
            lambda _settings: _FakeTokenProvider(recorded, fail=fail),
        )
        return recorded

    return _install


def _azure_client(client_factory: Callable[..., TestClient]) -> TestClient:
    """Build a client whose backend selection actually uses the Azure managed identity."""
    client = client_factory(
        storage_backend="azure_blob",
        queue_backend="container_apps_jobs",
        azure_storage_token_resource="https://storage.azure.com/",
        azure_arm_token_resource="https://management.azure.com/",
    )
    client.app.state.rag_index_dir = None  # isolate the identity check
    return client


def test_readyz_probes_managed_identity_for_every_selected_azure_audience(
    client_factory: Callable[..., TestClient],
    fake_token_provider: Callable[..., list[str]],
) -> None:
    recorded = fake_token_provider()
    response = _azure_client(client_factory).get("/readyz")

    assert response.status_code == 200
    check = readiness_check(response.json(), "azureIdentity")
    assert check["status"] == "ok"
    assert check["detail"] == "2 audience(s)"
    assert recorded == ["https://storage.azure.com/", "https://management.azure.com/"]


def test_readyz_is_503_when_the_managed_identity_cannot_issue_a_token(
    client_factory: Callable[..., TestClient],
    fake_token_provider: Callable[..., list[str]],
) -> None:
    """The prod IMDS misconfiguration was invisible precisely because nothing probed this."""
    fake_token_provider(fail=True)
    response = _azure_client(client_factory).get("/readyz")

    assert response.status_code == 503
    check = readiness_check(response.json(), "azureIdentity")
    assert check["status"] == "down"
    assert check["detail"] == "blob token unavailable"


def test_readyz_omits_the_identity_probe_for_local_backends(
    client_factory: Callable[..., TestClient], tmp_path: Path
) -> None:
    client = client_factory(storage_backend="local", queue_backend="local")
    client.app.state.rag_index_dir = tmp_path / "absent"
    response = client.get("/readyz")

    assert response.status_code == 200
    assert "azureIdentity" not in {check["name"] for check in response.json()["checks"]}

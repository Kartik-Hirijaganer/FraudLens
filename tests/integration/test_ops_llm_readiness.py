"""Integration tests for readiness over the SAR cascade's MODEL routes.

Readiness probes every ACTIVE stage of the selected SAR profile, resolving each stage's named
connection from the provider registry at probe time. These tests hold the two properties that makes
that safe to deploy: a cascade is ready only when EVERY active stage answers, and a stage the
selected profile does not use is never probed — so the standing deployment needs no secret for an
endpoint that exists only during an approved benchmark session.

Split from `test_ops.py` in release 0.5.0 under the 500-line cap (ADR-022); the dependency probes
that are not model routes stay there.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ops_probes import OkEngine, build_fixture_index, readiness_check

from fraudlens_backend.api import ops
from fraudlens_backend.api.ops import DependencyCheck


def test_readyz_reports_active_vllm_provider(
    client_factory: Callable[..., TestClient],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The live provider probe follows the selected SAR profile and resolved endpoint."""
    calls: list[tuple[str, dict[str, str] | None]] = []

    async def ok(
        url: str,
        _timeout: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> int:
        calls.append((url, headers))
        return 200

    monkeypatch.setenv("VLLM_AWQ_API_KEY", "synthetic-test-value")
    monkeypatch.setenv("VLLM_AWQ_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("VLLM_BF16_API_KEY", "synthetic-test-value")
    monkeypatch.setenv("VLLM_BF16_BASE_URL", "http://127.0.0.1:8001/v1")
    monkeypatch.setattr(ops, "_fetch_status", ok)
    client = client_factory(
        llm_mode="live",
        sar_config_file="llm/sar-vllm.yml",
        sar_profile="awq-bf16",
        auth_jwks_url="https://supabase.example.test/auth/v1/jwks",
        infisical_secrets_delivery="externally_injected",
        infisical_required_env_keys=["VLLM_AWQ_API_KEY"],
    )
    client.app.state.db_engine = OkEngine()
    client.app.state.rag_index_dir = build_fixture_index(
        tmp_path / "vllm-chroma", client.app.state.settings.rag_collection
    )

    response = client.get("/readyz")

    assert response.status_code == 200
    assert readiness_check(response.json(), "llmProvider") == {
        "name": "llmProvider",
        "status": "ok",
        "detail": "bf16",
    }
    # Both tiers are probed on their OWN injected endpoint: one `vllm` governance entry, two
    # named connections. Before release 0.5.0 both stages resolved to a single base URL.
    assert {url for url, _headers in calls} >= {
        "http://127.0.0.1:8000/v1/models",
        "http://127.0.0.1:8001/v1/models",
    }


async def _no_revalidation(*_args: object, **_kwargs: object) -> None:
    """Skip the live upstream re-derivation; this test is about WHICH stages are probed."""


@pytest.mark.asyncio
async def test_vllm_readiness_fails_closed_without_api_key(
    client_factory: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The selected provider is down when its configured key was not injected."""
    monkeypatch.delenv("VLLM_AWQ_API_KEY", raising=False)
    monkeypatch.setenv("VLLM_AWQ_BASE_URL", "http://127.0.0.1:8000/v1")
    client = client_factory(
        llm_mode="live", sar_config_file="llm/sar-vllm.yml", sar_profile="awq-bf16"
    )

    check = await ops._probe_llm_provider(client.app.state.settings, timeout=1.0)

    assert check == DependencyCheck(name="llmProvider", status="down", detail="awq")


@pytest.mark.asyncio
async def test_readiness_fails_closed_when_a_later_cascade_stage_is_unreachable(
    client_factory: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cascade is ready only when EVERY stage is: a dead BF16 tier is not a healthy service."""

    async def only_awq(url: str, _timeout: float, **_kwargs: object) -> int:
        return 200 if url.startswith("http://127.0.0.1:8000") else 503

    monkeypatch.setenv("VLLM_AWQ_API_KEY", "synthetic-test-value")
    monkeypatch.setenv("VLLM_AWQ_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("VLLM_BF16_API_KEY", "synthetic-test-value")
    monkeypatch.setenv("VLLM_BF16_BASE_URL", "http://127.0.0.1:8001/v1")
    monkeypatch.setattr(ops, "_fetch_status", only_awq)
    client = client_factory(
        llm_mode="live", sar_config_file="llm/sar-vllm.yml", sar_profile="awq-bf16"
    )

    check = await ops._probe_llm_provider(client.app.state.settings, timeout=1.0)

    assert check == DependencyCheck(name="llmProvider", status="down", detail="bf16")


@pytest.mark.asyncio
async def test_an_inactive_cascade_stage_needs_no_secret_and_never_fails_readiness(
    client_factory: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deployed profile must stay ready on a host that has never heard of RunPod.

    The same routing file declares the hosted deployed route and the two self-hosted benchmark
    tiers. If readiness probed every DECLARED stage rather than every ACTIVE one, Container Apps
    and AKS would both need `VLLM_*` secrets for endpoints that exist only during an approved
    benchmark session — and the standing deployment would report itself down whenever no GPU was
    rented (release 0.5.0 Phase 5 deployment parity).
    """
    probed: list[str] = []

    async def record(url: str, _timeout: float, **_kwargs: object) -> int:
        probed.append(url)
        return 200

    for name in (
        "VLLM_AWQ_API_KEY",
        "VLLM_AWQ_BASE_URL",
        "VLLM_BF16_API_KEY",
        "VLLM_BF16_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-test-value")
    monkeypatch.setattr(ops, "_fetch_status", record)
    monkeypatch.setattr(ops, "_revalidate_route_eligibility", _no_revalidation)
    client = client_factory(
        llm_mode="live", sar_config_file="llm/sar-vllm.yml", sar_profile="deployed-openrouter"
    )

    check = await ops._probe_llm_provider(client.app.state.settings, timeout=1.0)

    assert check == DependencyCheck(name="llmProvider", status="ok", detail="external")
    assert probed and not any("127.0.0.1:800" in url for url in probed)

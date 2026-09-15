"""Behavioral tests for the 5-minute cache over /readyz's two remote dependency probes.

At a 30-second platform probe cadence an uncached readiness endpoint makes thousands of outbound
Supabase and LLM-provider calls a day purely to answer a health check. Caching changes only how
often those dependencies are asked: the response envelope, the per-probe statuses, and the
fail-closed aggregate are all unchanged, and the cheap local checks still run every request.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fraudlens_backend.api import ops

_JWKS_URL = "https://supabase.example.test/auth/v1/jwks"


class _Clock:
    """Monotonic stand-in whose value only moves when a test advances it."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _jwks_client(
    client_factory: Callable[..., TestClient], tmp_path: Path, **overrides: object
) -> TestClient:
    """Build a client whose only remote probe is Supabase JWKS (Chroma and DB stay skipped)."""
    client = client_factory(auth_jwks_url=_JWKS_URL, **overrides)
    client.app.state.rag_index_dir = tmp_path / "absent"
    return client


def test_a_reachable_remote_probe_is_not_refetched_within_the_window(
    client_factory: Callable[..., TestClient], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    clock = _Clock()

    async def probe(url: str, _timeout: float) -> int:
        calls.append(url)
        return 200

    monkeypatch.setattr(ops, "_fetch_status", probe)
    monkeypatch.setattr(ops.time, "monotonic", clock)
    client = _jwks_client(client_factory, tmp_path)

    first = client.get("/readyz")
    clock.advance(ops._REMOTE_PROBE_CACHE_SECONDS - 1)
    second = client.get("/readyz")

    assert len(calls) == 1
    assert first.json() == second.json()
    assert second.status_code == 200


def test_the_remote_probe_runs_again_once_the_entry_expires(
    client_factory: Callable[..., TestClient], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    clock = _Clock()

    async def probe(url: str, _timeout: float) -> int:
        calls.append(url)
        return 200

    monkeypatch.setattr(ops, "_fetch_status", probe)
    monkeypatch.setattr(ops.time, "monotonic", clock)
    client = _jwks_client(client_factory, tmp_path)

    client.get("/readyz")
    clock.advance(ops._REMOTE_PROBE_CACHE_SECONDS)
    client.get("/readyz")

    assert len(calls) == 2


def test_a_provider_outage_still_fails_closed_and_recovery_is_observed_after_expiry(
    client_factory: Callable[..., TestClient], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock()
    reachable = [False]

    async def probe(_url: str, _timeout: float) -> int:
        if not reachable[0]:
            raise OSError("unreachable")
        return 200

    monkeypatch.setattr(ops, "_fetch_status", probe)
    monkeypatch.setattr(ops.time, "monotonic", clock)
    client = _jwks_client(client_factory, tmp_path)

    outage = client.get("/readyz")
    assert outage.status_code == 503
    assert _named(outage, "supabaseAuth")["status"] == "down"

    # Recovery inside the window is not yet visible: the cached refusal keeps readiness closed.
    reachable[0] = True
    still_cached = client.get("/readyz")
    assert still_cached.status_code == 503
    assert _named(still_cached, "supabaseAuth")["status"] == "down"

    clock.advance(ops._REMOTE_PROBE_CACHE_SECONDS)
    recovered = client.get("/readyz")
    assert recovered.status_code == 200
    assert _named(recovered, "supabaseAuth")["status"] == "ok"


def test_local_checks_are_never_served_from_the_cache(
    client_factory: Callable[..., TestClient], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock()
    pings: list[int] = []

    async def probe(_url: str, _timeout: float) -> int:
        return 200

    async def ping(_engine: object, *, timeout_seconds: float) -> None:
        del timeout_seconds
        pings.append(1)

    monkeypatch.setattr(ops, "_fetch_status", probe)
    monkeypatch.setattr(ops, "ping_database", ping)
    monkeypatch.setattr(ops.time, "monotonic", clock)
    client = _jwks_client(client_factory, tmp_path)
    client.app.state.db_engine = object()

    client.get("/readyz")
    client.get("/readyz")

    assert len(pings) == 2


def test_each_app_keeps_its_own_probe_cache(
    client_factory: Callable[..., TestClient], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    clock = _Clock()

    async def probe(url: str, _timeout: float) -> int:
        calls.append(url)
        return 200

    monkeypatch.setattr(ops, "_fetch_status", probe)
    monkeypatch.setattr(ops.time, "monotonic", clock)

    _jwks_client(client_factory, tmp_path).get("/readyz")
    _jwks_client(client_factory, tmp_path).get("/readyz")

    assert len(calls) == 2


def _named(response: object, name: str) -> dict[str, str]:
    """Return one dependency check from a /readyz body."""
    body = response.json()  # type: ignore[attr-defined]
    return next(check for check in body["checks"] if check["name"] == name)

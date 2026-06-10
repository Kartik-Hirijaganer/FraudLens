"""Post-deploy smoke tests (deploy gate). Marked `smoke` and deselected from the
normal suite (`-m "not smoke"`); the deploy workflow runs `pytest -m smoke` against
the live URL via SMOKE_BASE_URL. They hit only the unprefixed ops probes."""

from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.smoke

BASE_URL = os.environ.get("SMOKE_BASE_URL", "")


@pytest.mark.skipif(not BASE_URL, reason="SMOKE_BASE_URL not set")
def test_healthz_live() -> None:
    response = httpx.get(f"{BASE_URL}/healthz", timeout=10.0)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.skipif(not BASE_URL, reason="SMOKE_BASE_URL not set")
def test_readyz_live() -> None:
    response = httpx.get(f"{BASE_URL}/readyz", timeout=10.0)
    assert response.status_code in (200, 503)

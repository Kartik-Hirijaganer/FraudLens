"""Shared fixtures for the Phase 13 security suite (plan §16 Phase 13, §17.1 "Security").

The suite is the consolidated, deploy-gating security check: it re-asserts the cross-cutting
guarantees (fail-closed auth, tenant isolation, no-leak, input safety) end-to-end and covers the
hardening code Phase 13 adds (path-aware CSP, the per-route rate limiter). These helpers mirror
the integration-test pattern (httpx + ASGITransport sharing the event loop with the in-memory
async DB) so a DB-wired app can be exercised over real HTTP.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from fraudlens_backend.api.deps import AccessClaims, TokenVerifier
from fraudlens_backend.main import create_app
from fraudlens_backend.settings import AppSettings


@pytest.fixture
def make_security_app(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[Any],
) -> Callable[..., Any]:
    """Return a factory building an app wired to the in-memory test engine/sessionmaker."""

    def _make(**overrides: Any) -> Any:
        app = create_app(make_settings(**overrides))
        app.state.db_engine = db_engine
        app.state.db_sessionmaker = db_sessionmaker
        return app

    return _make


@pytest.fixture
def accept() -> Callable[..., Callable[[], TokenVerifier]]:
    """Return a factory producing a verifier override that mints the given agency/role claim."""

    def _accept(
        agency_id: str, *, role: str = "analyst", user_id: str | None = None
    ) -> Callable[[], TokenVerifier]:
        def _factory() -> TokenVerifier:
            def _verify(_token: str) -> AccessClaims:
                return AccessClaims(agency_id=agency_id, role=role, user_id=user_id)

            return _verify

        return _factory

    return _accept


@pytest.fixture
def aclient() -> Callable[[Any], httpx.AsyncClient]:
    """Return a factory building an in-process AsyncClient over an ASGI app (DB-loop safe)."""

    def _client(app: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    return _client

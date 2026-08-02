"""DB-backed tests for GET /api/v1/agencies/{agencyId} (plan §16 Phase 2: "wire
/agencies/{id} to DB"). Uses httpx + ASGITransport so the request runs in the same event
loop as the async SQLite engine. Covers: an existing agency → 200 with the lookup body, a
missing agency → 404 (no existence leak), and the dev bypass resolving the seeded demo
agency end-to-end. (401/403/503 paths that short-circuit before the DB live in
test_api_v1.py.)"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
import pytest
from fastapi import HTTPException
from portfolio_demo_identity import DEMO_AGENCY_ID, DEMO_AGENCY_NAME, DEMO_AGENCY_SLUG
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fraudlens_backend.api.deps import AccessClaims, TokenVerifier, get_token_verifier
from fraudlens_backend.api.v1.router import read_agency
from fraudlens_backend.db.models import Agency
from fraudlens_backend.main import create_app
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.settings import AppSettings

AUTH = {"Authorization": "Bearer test-token"}


def _build_app(
    settings: AppSettings,
    engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> object:
    """Build an app and wire the in-memory test engine/sessionmaker onto its state."""
    app = create_app(settings)
    app.state.db_engine = engine
    app.state.db_sessionmaker = sessionmaker
    return app


def _accept(agency_id: str) -> Callable[[], TokenVerifier]:
    """Override factory: a verifier that accepts any token as the given agency claim."""
    return lambda: lambda _token: AccessClaims(agency_id=agency_id)


def _client(app: object) -> httpx.AsyncClient:
    """An AsyncClient driving the ASGI app in-process (same event loop as the DB)."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _seed(sessionmaker: async_sessionmaker[AsyncSession], *agencies: Agency) -> None:
    """Insert and commit agencies via a short-lived session (closed before the request)."""
    async with sessionmaker() as session:
        for agency in agencies:
            session.add(agency)
        await session.commit()


async def test_existing_agency_returns_200(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    agency = Agency(id=uuid.uuid4(), name="Acme", slug="acme")
    await _seed(db_sessionmaker, agency)
    app = _build_app(make_settings(), db_engine, db_sessionmaker)
    app.dependency_overrides[get_token_verifier] = _accept(str(agency.id))  # type: ignore[attr-defined]
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/agencies/{agency.id}", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"agencyId": str(agency.id), "name": "Acme", "slug": "acme"}


async def test_missing_agency_returns_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    missing = uuid.uuid4()
    app = _build_app(make_settings(), db_engine, db_sessionmaker)
    app.dependency_overrides[get_token_verifier] = _accept(str(missing))  # type: ignore[attr-defined]
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/agencies/{missing}", headers=AUTH)
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


async def test_read_agency_handler_returns_and_404s(db_session: AsyncSession) -> None:
    # Direct-call coverage of the handler body (the httpx path above isn't traced by coverage).
    agency = Agency(id=uuid.uuid4(), name="Acme", slug="acme-direct")
    db_session.add(agency)
    await db_session.flush()
    found = await read_agency(TenantContext(agency_id=str(agency.id)), db_session)
    assert found.slug == "acme-direct"
    with pytest.raises(HTTPException) as excinfo:
        await read_agency(TenantContext(agency_id=str(uuid.uuid4())), db_session)
    assert excinfo.value.status_code == 404


async def test_dev_bypass_resolves_seeded_demo_agency(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(
        db_sessionmaker,
        Agency(id=DEMO_AGENCY_ID, name=DEMO_AGENCY_NAME, slug=DEMO_AGENCY_SLUG),
    )
    app = _build_app(
        make_settings(environment="dev", auth_dev_bypass=True), db_engine, db_sessionmaker
    )
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/agencies/{DEMO_AGENCY_ID}")  # no token; dev bypass
    assert resp.status_code == 200
    assert resp.json()["slug"] == DEMO_AGENCY_SLUG

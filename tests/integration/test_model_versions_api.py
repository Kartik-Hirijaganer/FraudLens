"""Model-registry API tests (plan §5.3 / §16 Phase 5: GET /api/v1/model-versions). Verify the
seeded registry lists with the active version label, detail-by-id works, an unknown id returns
the model_version_not_found envelope (404), and the surface fails closed without a JWT (401)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fraudlens_backend.main import create_app
from fraudlens_backend.settings import AppSettings
from seed import seed


def _build_app(settings: AppSettings, engine: AsyncEngine, sm: async_sessionmaker[AsyncSession]):
    """Build an app wired to the in-memory test engine/sessionmaker."""
    app = create_app(settings)
    app.state.db_engine = engine
    app.state.db_sessionmaker = sm
    return app


def _client(app: object) -> httpx.AsyncClient:
    """An AsyncClient driving the ASGI app in-process (same loop as the DB)."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _seed(sm: async_sessionmaker[AsyncSession]) -> None:
    """Seed the demo dataset (incl. the active fixture model version + deployment)."""
    async with sm() as session:
        await seed(session)
        await session.commit()


async def test_list_returns_registry_with_active_label(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.get("/api/v1/model-versions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["activeVersionLabel"] == "v0-fixture"
    assert len(body["versions"]) == 1
    version = body["versions"][0]
    assert version["versionLabel"] == "v0-fixture"
    assert version["status"] == "active"
    assert "agencyId" not in version  # the registry is platform-global (ADR-015)
    assert version["metrics"]["pr_auc"] >= 0.45


async def test_get_detail_and_unknown_is_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)
    async with _client(app) as client:
        listing = await client.get("/api/v1/model-versions")
        version_id = listing.json()["versions"][0]["versionId"]
        detail = await client.get(f"/api/v1/model-versions/{version_id}")
        missing = await client.get(f"/api/v1/model-versions/{uuid.uuid4()}")
    assert detail.status_code == 200
    assert detail.json()["versionLabel"] == "v0-fixture"
    assert missing.status_code == 404
    assert missing.json()["code"] == "model_version_not_found"


async def test_requires_authentication(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(db_sessionmaker)
    app = _build_app(make_settings(auth_dev_bypass=False), db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.get("/api/v1/model-versions")
    assert resp.status_code == 401

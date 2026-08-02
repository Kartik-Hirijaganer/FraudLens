"""Integration tests for admin config and non-production dev utility APIs."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from portfolio_demo_identity import DEMO_AGENCY_ID, DEMO_BYPASS_USER_ID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fraudlens_backend.api.deps import AccessClaims, get_token_verifier
from fraudlens_backend.db.models import AuditLog, SystemConfig
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
    """An AsyncClient driving the ASGI app in-process."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_config_patch_lists_and_audits_value_free_metadata(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        await seed(session)
        await session.commit()
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)

    async with _client(app) as client:
        patched = await client.patch(
            "/api/v1/config",
            json={"key": "alertThreshold", "value": 0.73, "agencyScoped": True},
        )
        listed = await client.get("/api/v1/config")

    assert patched.status_code == 200
    assert patched.json()["entry"]["agencyId"] == str(DEMO_AGENCY_ID)
    assert listed.status_code == 200
    assert any(row["key"] == "alertThreshold" for row in listed.json()["config"])
    async with db_sessionmaker() as session:
        row = (
            await session.execute(
                select(SystemConfig).where(
                    SystemConfig.agency_id == DEMO_AGENCY_ID,
                    SystemConfig.key == "alertThreshold",
                )
            )
        ).scalar_one()
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.agency_id == DEMO_AGENCY_ID,
                    AuditLog.action == "config.update",
                )
            )
        ).scalar_one()
    assert row.value == 0.73
    assert audit.meta == {"key": "alertThreshold", "scope": "agency"}


async def test_dev_utility_routes_are_admin_audited_in_dev(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        await seed(session)
        await session.commit()
    app = _build_app(make_settings(auth_dev_bypass=True), db_engine, db_sessionmaker)

    async with _client(app) as client:
        seed_response = await client.post("/api/v1/dev/seed")
        reset_response = await client.post("/api/v1/dev/reset")

    assert seed_response.status_code == 200
    assert reset_response.status_code == 200
    assert seed_response.json() == {
        "action": "seed",
        "status": "accepted",
        "agencyId": str(DEMO_AGENCY_ID),
    }
    async with db_sessionmaker() as session:
        actions = {
            row.action
            for row in (
                await session.execute(select(AuditLog).where(AuditLog.agency_id == DEMO_AGENCY_ID))
            )
            .scalars()
            .all()
        }
    assert {"dev.seed", "dev.reset"} <= actions


async def test_dev_utility_routes_are_disabled_in_prod_with_real_auth(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    app = _build_app(make_settings(environment="prod"), db_engine, db_sessionmaker)
    app.dependency_overrides[get_token_verifier] = lambda: (
        lambda _token: AccessClaims(
            agency_id=str(DEMO_AGENCY_ID),
            user_id=str(DEMO_BYPASS_USER_ID),
            role="admin",
        )
    )

    async with _client(app) as client:
        response = await client.post(
            "/api/v1/dev/seed", headers={"Authorization": "Bearer prod-token"}
        )

    assert response.status_code == 403
    assert response.json()["code"] == "dev_utility_disabled"

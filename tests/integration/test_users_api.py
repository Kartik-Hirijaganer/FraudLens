"""Integration tests for Track B real-auth user identity and admin invite APIs."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fraudlens_backend.api.deps import AccessClaims, get_token_verifier
from fraudlens_backend.api.v1.users import get_supabase_admin
from fraudlens_backend.db.models import AuditLog, User, UserRole
from fraudlens_backend.demo import DEMO_AGENCY_ID, DEMO_USER_ID, DEMO_USERS
from fraudlens_backend.main import create_app
from fraudlens_backend.settings import AppSettings
from seed import seed

_INVITED_USER_ID = uuid.UUID("66666666-6666-4666-8666-666666666666")


class _SupabaseAdminStub:
    """Mock Supabase admin client returning a deterministic invited auth uid."""

    async def invite_user(self, *, email: str) -> uuid.UUID:
        """Return the fake uid; email is accepted but never logged or asserted in errors."""
        return _INVITED_USER_ID


def _build_app(
    settings: AppSettings,
    engine: AsyncEngine,
    sm: async_sessionmaker[AsyncSession],
    claims: AccessClaims | None,
):
    """Build an app wired to the in-memory test engine/sessionmaker and optional claims."""
    app = create_app(settings)
    app.state.db_engine = engine
    app.state.db_sessionmaker = sm
    if claims is not None:
        app.dependency_overrides[get_token_verifier] = lambda: lambda _token: claims
    app.dependency_overrides[get_supabase_admin] = _SupabaseAdminStub
    return app


def _client(app: object) -> httpx.AsyncClient:
    """Return an AsyncClient driving the ASGI app in-process."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _demo_admin_id() -> uuid.UUID:
    """Return the seeded demo admin id."""
    return next(spec.user_id for spec in DEMO_USERS if spec.role == UserRole.ADMIN)


async def test_me_returns_claim_role_and_provisioned_email(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        await seed(session)
        await session.commit()
    app = _build_app(
        make_settings(auth_dev_bypass=False),
        db_engine,
        db_sessionmaker,
        AccessClaims(
            agency_id=str(DEMO_AGENCY_ID),
            user_id=str(DEMO_USER_ID),
            role=UserRole.ANALYST.value,
        ),
    )

    async with _client(app) as client:
        response = await client.get("/api/v1/me", headers={"Authorization": "Bearer token"})

    assert response.status_code == 200
    assert response.json() == {
        "email": "analyst@demo-agency.test",
        "role": "analyst",
        "agencyId": str(DEMO_AGENCY_ID),
    }


async def test_admin_invites_user_and_reconciles_public_users_row(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        await seed(session)
        await session.commit()
    app = _build_app(
        make_settings(auth_dev_bypass=False),
        db_engine,
        db_sessionmaker,
        AccessClaims(
            agency_id=str(DEMO_AGENCY_ID),
            user_id=str(_demo_admin_id()),
            role=UserRole.ADMIN.value,
        ),
    )

    async with _client(app) as client:
        response = await client.post(
            "/api/v1/users",
            headers={"Authorization": "Bearer token"},
            json={
                "email": "new.reviewer@example.test",
                "displayName": "New Reviewer",
                "role": "reviewer",
            },
        )

    assert response.status_code == 201
    assert response.json() == {
        "userId": str(_INVITED_USER_ID),
        "email": "new.reviewer@example.test",
        "displayName": "New Reviewer",
        "role": "reviewer",
        "agencyId": str(DEMO_AGENCY_ID),
    }
    async with db_sessionmaker() as session:
        user = await session.get(User, _INVITED_USER_ID)
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.agency_id == DEMO_AGENCY_ID,
                    AuditLog.action == "user.invite",
                )
            )
        ).scalar_one()
    assert user is not None
    assert user.agency_id == DEMO_AGENCY_ID
    assert user.role is UserRole.REVIEWER
    assert audit.meta == {"role": "reviewer"}


async def test_non_admin_cannot_invite_users(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    app = _build_app(
        make_settings(auth_dev_bypass=False),
        db_engine,
        db_sessionmaker,
        AccessClaims(
            agency_id=str(DEMO_AGENCY_ID),
            user_id=str(DEMO_USER_ID),
            role=UserRole.ANALYST.value,
        ),
    )

    async with _client(app) as client:
        response = await client.post(
            "/api/v1/users",
            headers={"Authorization": "Bearer token"},
            json={"email": "blocked@example.test", "displayName": "Blocked", "role": "auditor"},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "admin_role_required"

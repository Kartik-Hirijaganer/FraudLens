"""Integration tests for idempotent live-demo Supabase identity reconciliation."""

from __future__ import annotations

import uuid

from portfolio_demo_identity import (
    DEMO_AGENCY_ID,
    DEMO_AGENCY_NAME,
    DEMO_AGENCY_SLUG,
    DEMO_PERSONAS,
    demo_history_email,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.db.models import Agency, AuditLog, User
from fraudlens_backend.services.supabase_admin import SupabaseAuthAppMetadata
from provision_demo_auth import provision_demo_auth

_PASSWORD = "synthetic-test-password"
_AUTH_IDS = {
    persona.email: uuid.uuid5(uuid.NAMESPACE_URL, f"fraudlens-auth:{persona.email}")
    for persona in DEMO_PERSONAS
}


class _SupabasePasswordAdminStub:
    """Return stable auth UUIDs for the configured synthetic demo emails."""

    async def ensure_password_user(
        self,
        *,
        email: str,
        password: str,
        app_metadata: SupabaseAuthAppMetadata,
    ) -> uuid.UUID:
        """Resolve a deterministic UUID; assert the JWT carries the configured agency id."""
        assert password == _PASSWORD
        assert app_metadata.agency_id == str(DEMO_AGENCY_ID)
        assert app_metadata.user_role
        return _AUTH_IDS[email]


async def _seed_fixed_users(session: AsyncSession) -> None:
    """Seed the configured fixed actors to exercise history preservation."""
    session.add(Agency(id=DEMO_AGENCY_ID, name=DEMO_AGENCY_NAME, slug=DEMO_AGENCY_SLUG))
    session.add_all(
        User(
            id=persona.seed_user_id,
            agency_id=DEMO_AGENCY_ID,
            email=persona.email,
            display_name=persona.display_name,
            role=persona.role,
        )
        for persona in DEMO_PERSONAS
    )
    await session.flush()


async def test_provision_demo_auth_covers_the_demo_tenant_and_is_idempotent(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        await _seed_fixed_users(session)
        first = await provision_demo_auth(session, _SupabasePasswordAdminStub(), password=_PASSWORD)
        await session.commit()

    assert first.auth_users == len(DEMO_PERSONAS)
    assert first.app_users == len(DEMO_PERSONAS)
    # First run reconciles every persona: each pre-seeded fixed actor is re-pointed at its auth id.
    assert first.reconciled_users == len(DEMO_PERSONAS)

    async with db_sessionmaker() as session:
        users = (
            await session.execute(select(User).where(User.agency_id == DEMO_AGENCY_ID))
        ).scalars()
        users_by_email = {user.email: user for user in users}
        audit_count = await session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "demo_auth.provision")
        )
        second = await provision_demo_auth(
            session, _SupabasePasswordAdminStub(), password=_PASSWORD
        )
        await session.commit()

    # Every persona resolves to its auth UUID under the ONE configured agency.
    for persona in DEMO_PERSONAS:
        canonical = users_by_email[persona.email]
        assert canonical.id == _AUTH_IDS[persona.email]
        assert canonical.agency_id == DEMO_AGENCY_ID
        assert canonical.role == persona.role

    # The pre-seeded actors survive as history-only rows (audit FKs intact).
    for persona in DEMO_PERSONAS:
        historical = users_by_email[demo_history_email(persona.role)]
        assert historical.id == persona.seed_user_id
        assert historical.agency_id == DEMO_AGENCY_ID

    assert audit_count == len(DEMO_PERSONAS)
    assert second.reconciled_users == 0


async def test_provision_demo_auth_creates_the_agency_when_absent(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A fresh database gets the configured tenant before any auth-backed user is inserted."""
    async with db_sessionmaker() as session:
        summary = await provision_demo_auth(
            session, _SupabasePasswordAdminStub(), password=_PASSWORD
        )
        await session.commit()

    async with db_sessionmaker() as session:
        agency = await session.get(Agency, DEMO_AGENCY_ID)
        assert agency is not None
        assert agency.slug == DEMO_AGENCY_SLUG
        # No prior seed actor existed, so nothing is renamed to a history-only email.
        emails = (
            await session.execute(select(User.email).where(User.agency_id == DEMO_AGENCY_ID))
        ).scalars()
        assert not [email for email in emails if email.startswith("seed-")]
    assert summary.reconciled_users == len(DEMO_PERSONAS)

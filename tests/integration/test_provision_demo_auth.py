"""Integration tests for idempotent live-demo Supabase identity reconciliation."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.db.models import Agency, AuditLog, User
from fraudlens_backend.demo import (
    AML_DEMO_AGENCY_TWO_ID,
    DEMO_AGENCY_ID,
    DEMO_AGENCY_NAME,
    DEMO_AGENCY_SLUG,
    DEMO_USERS,
    LIVE_DEMO_USERS,
)
from fraudlens_backend.services.supabase_admin import SupabaseAuthAppMetadata
from provision_demo_auth import provision_demo_auth

_AUTH_IDS = {
    spec.email: uuid.uuid5(uuid.NAMESPACE_URL, f"fraudlens-auth:{spec.email}")
    for spec in LIVE_DEMO_USERS
}
_AGENCY_BY_EMAIL = {spec.email: spec.agency_id for spec in LIVE_DEMO_USERS}


class _SupabasePasswordAdminStub:
    """Return stable auth UUIDs for the synthetic demo emails across both tenants."""

    async def ensure_password_user(
        self,
        *,
        email: str,
        password: str,
        app_metadata: SupabaseAuthAppMetadata,
    ) -> uuid.UUID:
        """Resolve a deterministic UUID; assert the JWT carries the user's OWN agency id."""
        assert password
        assert app_metadata.agency_id == str(_AGENCY_BY_EMAIL[email])
        assert app_metadata.user_role
        return _AUTH_IDS[email]


async def _seed_fixed_users(session: AsyncSession) -> None:
    """Seed only the primary agency's fixed actors to exercise history preservation."""
    session.add(Agency(id=DEMO_AGENCY_ID, name=DEMO_AGENCY_NAME, slug=DEMO_AGENCY_SLUG))
    session.add_all(
        User(
            id=spec.user_id,
            agency_id=DEMO_AGENCY_ID,
            email=spec.email,
            display_name=spec.display_name,
            role=spec.role,
        )
        for spec in DEMO_USERS
    )
    await session.flush()


async def test_provision_demo_auth_covers_both_tenants_and_is_idempotent(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        await _seed_fixed_users(session)
        first = await provision_demo_auth(session, _SupabasePasswordAdminStub())
        await session.commit()

    assert first.auth_users == len(LIVE_DEMO_USERS)
    assert first.app_users == len(LIVE_DEMO_USERS)
    # First run reconciles every user: the primary tenant's four are re-pointed, Agency Two's is
    # created (its tenant did not exist before this run).
    assert first.reconciled_users == len(LIVE_DEMO_USERS)

    async with db_sessionmaker() as session:
        # Provisioning created the second tenant it needed before inserting Agency Two's analyst.
        assert await session.get(Agency, AML_DEMO_AGENCY_TWO_ID) is not None
        users = (
            await session.execute(
                select(User).where(User.agency_id.in_([DEMO_AGENCY_ID, AML_DEMO_AGENCY_TWO_ID]))
            )
        ).scalars()
        users_by_email = {user.email: user for user in users}
        audit_count = await session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "demo_auth.provision")
        )
        second = await provision_demo_auth(session, _SupabasePasswordAdminStub())
        await session.commit()

    # Every persona resolves to its auth UUID under its OWN agency (the isolation invariant).
    for spec in LIVE_DEMO_USERS:
        canonical = users_by_email[spec.email]
        assert canonical.id == _AUTH_IDS[spec.email]
        assert canonical.agency_id == spec.agency_id
        assert canonical.role == spec.role

    # The primary tenant's pre-seeded actors survive as history-only rows (audit FKs intact).
    for spec in DEMO_USERS:
        historical = users_by_email[f"seed-{spec.role.value}@{DEMO_AGENCY_SLUG}.test"]
        assert historical.id == spec.user_id
        assert historical.agency_id == DEMO_AGENCY_ID

    # Agency Two's analyst is a fresh identity — no prior seed actor to preserve.
    assert "analyst@aml-demo-agency-two.test" in users_by_email
    assert "seed-analyst@aml-demo-agency-two.test" not in users_by_email

    assert audit_count == len(LIVE_DEMO_USERS)
    assert second.reconciled_users == 0

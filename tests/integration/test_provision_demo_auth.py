"""Integration tests for idempotent live-demo Supabase identity reconciliation."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.db.models import Agency, AuditLog, User
from fraudlens_backend.demo import (
    DEMO_AGENCY_ID,
    DEMO_AGENCY_NAME,
    DEMO_AGENCY_SLUG,
    DEMO_USERS,
)
from fraudlens_backend.services.supabase_admin import SupabaseAuthAppMetadata
from provision_demo_auth import provision_demo_auth

_AUTH_IDS = {
    spec.email: uuid.uuid5(uuid.NAMESPACE_URL, f"fraudlens-auth:{spec.email}")
    for spec in DEMO_USERS
}


class _SupabasePasswordAdminStub:
    """Return stable auth UUIDs for the four synthetic demo emails."""

    async def ensure_password_user(
        self,
        *,
        email: str,
        password: str,
        app_metadata: SupabaseAuthAppMetadata,
    ) -> uuid.UUID:
        """Resolve a deterministic UUID while asserting a non-empty synthetic credential."""
        assert password
        assert app_metadata.agency_id == str(DEMO_AGENCY_ID)
        assert app_metadata.user_role
        return _AUTH_IDS[email]


async def _seed_fixed_users(session: AsyncSession) -> None:
    """Create only the fixed agency/users needed to exercise history preservation."""
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


async def test_provision_demo_auth_preserves_history_and_is_idempotent(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        await _seed_fixed_users(session)
        first = await provision_demo_auth(session, _SupabasePasswordAdminStub())
        await session.commit()

    assert first.auth_users == len(DEMO_USERS)
    assert first.app_users == len(DEMO_USERS)
    assert first.reconciled_users == len(DEMO_USERS)

    async with db_sessionmaker() as session:
        users = (
            await session.execute(select(User).where(User.agency_id == DEMO_AGENCY_ID))
        ).scalars()
        users_by_email = {user.email: user for user in users}
        audit_count = await session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.agency_id == DEMO_AGENCY_ID,
                AuditLog.action == "demo_auth.provision",
            )
        )
        second = await provision_demo_auth(session, _SupabasePasswordAdminStub())
        await session.commit()

    for spec in DEMO_USERS:
        canonical = users_by_email[spec.email]
        historical = users_by_email[f"seed-{spec.role.value}@demo-agency.test"]
        assert canonical.id == _AUTH_IDS[spec.email]
        assert canonical.agency_id == DEMO_AGENCY_ID
        assert canonical.role == spec.role
        assert historical.id == spec.user_id
    assert audit_count == len(DEMO_USERS)
    assert second.reconciled_users == 0

"""Summary: Tenant-scoped user repository for real-auth provisioning. It resolves
`public.users` rows by Supabase auth uid or email within the current `agency_id`, and
upserts admin-invited users so `auth.users.id` and `public.users.id` stay reconciled.

Key classes:
- UserRepository: user lookup and upsert operations, scoped by agency_id.

Key functions:
- (none)

Notes:
- Every query includes `agency_id`; a token subject from another tenant resolves to no row.
- The repository writes only the app-owned `public.users` row. Supabase Auth user creation
  remains in the dedicated admin client wrapper.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import User, UserRole
from fraudlens_backend.db.repositories.base import TenantScopedRepository


class UserRepository(TenantScopedRepository[User]):
    """Data access for tenant-scoped users."""

    def __init__(self, session: AsyncSession, agency_id: uuid.UUID) -> None:
        """Bind the repository to a single agency scope."""
        super().__init__(session, User, agency_id)
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Return a user by id when it belongs to this agency, otherwise None."""
        return await self.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Return a user by email within this agency, otherwise None."""
        stmt = select(User).where(User.agency_id == self.agency_id, User.email == email)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_invited_user(
        self,
        *,
        user_id: uuid.UUID,
        email: str,
        display_name: str,
        role: UserRole,
    ) -> User:
        """Create or update the user row that mirrors a Supabase Auth identity."""
        user = await self.get_by_id(user_id)
        if user is None:
            user = User(
                id=user_id,
                agency_id=self.agency_id,
                email=email,
                display_name=display_name,
                role=role,
            )
            self._session.add(user)
        else:
            user.email = email
            user.display_name = display_name
            user.role = role
        await self._session.flush()
        return user

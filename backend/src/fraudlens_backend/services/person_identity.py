"""Summary: Server-owned identity resolution and trusted provisioning outside application APIs.

Key classes:
- WorkflowActor: verified account, canonical person and persisted role.

Key functions:
- resolve_actor: fail closed for missing, cross-tenant or unmapped accounts.
- provision_person_link: trusted seed/operator binding; refuses all remaps.

Notes:
- No request field or email comparison establishes person identity.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import AccountPerson, Person, User
from fraudlens_backend.db.models.enums import UserRole
from fraudlens_backend.models.errors import AppError


class WorkflowActor(BaseModel):
    """Identity loaded from trusted tenant records on every mutation."""

    account_id: uuid.UUID = Field(..., description="Verified account id.")
    person_id: uuid.UUID = Field(..., description="Canonical trusted person id.")
    role: UserRole = Field(..., description="Server-owned application role.")


async def resolve_actor(
    session: AsyncSession, agency_id: uuid.UUID, account_id: uuid.UUID
) -> WorkflowActor:
    """Resolve trusted identity; invitations without a binding cannot mutate cases."""
    row = (
        await session.execute(
            select(User, AccountPerson.person_id)
            .join(
                AccountPerson,
                (AccountPerson.account_id == User.id) & (AccountPerson.agency_id == User.agency_id),
            )
            .where(User.agency_id == agency_id, User.id == account_id)
        )
    ).one_or_none()
    if row is None:
        raise AppError("person_mapping_required")
    user, person_id = row
    return WorkflowActor(account_id=user.id, person_id=person_id, role=user.role)


async def provision_person_link(
    session: AsyncSession, *, agency_id: uuid.UUID, account_id: uuid.UUID, person_id: uuid.UUID
) -> None:
    """Bind a provisioned account once; intentionally absent from application-admin APIs."""
    user = (
        await session.execute(
            select(User).where(User.agency_id == agency_id, User.id == account_id)
        )
    ).scalar_one_or_none()
    if user is None:
        raise AppError("user_not_provisioned")
    existing = (
        await session.execute(
            select(AccountPerson).where(
                AccountPerson.agency_id == agency_id, AccountPerson.account_id == account_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.person_id != person_id:
            raise AppError("person_link_immutable")
        return
    person = (
        await session.execute(
            select(Person).where(Person.agency_id == agency_id, Person.id == person_id)
        )
    ).scalar_one_or_none()
    if person is None:
        try:
            async with session.begin_nested():
                session.add(Person(id=person_id, agency_id=agency_id))
                await session.flush()
        except IntegrityError as exc:
            # A globally unique person id already bound to another tenant cannot be linked here.
            raise AppError("person_link_immutable") from exc
    session.add(AccountPerson(agency_id=agency_id, account_id=account_id, person_id=person_id))
    await session.flush()

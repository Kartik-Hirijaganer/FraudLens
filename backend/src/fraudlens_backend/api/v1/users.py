"""Summary: Real-auth user endpoints for Track B. `GET /me` returns the frontend's
trusted session identity after JWT verification, and `POST /users` lets an admin invite a
new Supabase Auth user while creating the matching tenant-scoped `public.users` row.

Key classes:
- (none)

Key functions:
- get_supabase_admin:
- get_current_user: GET /api/v1/me, role source for the frontend.
- invite_user: POST /api/v1/users, admin-only invite + row reconciliation.

Notes:
- The invite route scopes the created user to the inviting admin's `agency_id`; no tenant id is
accepted from the client. Supabase service-role credentials are read from Infisical-backed env.
- The audit metadata records role only, not email, to keep logs free of identity details.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from fraudlens_backend.api.deps import (
    DbSessionDep,
    SettingsDep,
    audit_writer,
    get_admin_tenant,
    get_tenant,
    require_actor,
)
from fraudlens_backend.db.models import UserRole
from fraudlens_backend.db.repositories import UserRepository
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.models.users import (
    CurrentUserResponse,
    UserInviteRequest,
    UserInviteResponse,
)
from fraudlens_backend.services.supabase_admin import SupabaseAdminClient, SupabaseAdminError

router = APIRouter(tags=["users"])

TenantDep = Annotated[TenantContext, Depends(get_tenant)]
AdminDep = Annotated[TenantContext, Depends(get_admin_tenant)]


def get_supabase_admin(settings: SettingsDep) -> SupabaseAdminClient:
    """Return the configured Supabase admin client, or raise a safe app error."""
    try:
        return SupabaseAdminClient.from_settings(settings)
    except SupabaseAdminError as exc:
        raise AppError("user_invite_failed") from exc


SupabaseAdminDep = Annotated[SupabaseAdminClient, Depends(get_supabase_admin)]


@router.get("/me", response_model=CurrentUserResponse)
async def get_current_user(tenant: TenantDep, session: DbSessionDep) -> CurrentUserResponse:
    """Return the verified caller identity from claims plus the provisioned users row."""
    if tenant.user_id is None:
        raise AppError("acting_user_required")
    try:
        user_id = uuid.UUID(tenant.user_id)
        agency_id = uuid.UUID(tenant.agency_id)
    except ValueError as exc:
        raise AppError("acting_user_required") from exc
    user = await UserRepository(session, agency_id).get_by_id(user_id)
    if user is None:
        raise AppError("user_not_provisioned")
    try:
        role = UserRole(tenant.role)
    except ValueError as exc:
        raise AppError("user_not_provisioned") from exc
    return CurrentUserResponse(email=user.email, role=role, agency_id=tenant.agency_id)


@router.post("/users", response_model=UserInviteResponse, status_code=201)
async def invite_user(
    payload: UserInviteRequest,
    request: Request,
    tenant: AdminDep,
    session: DbSessionDep,
    supabase: SupabaseAdminDep,
) -> UserInviteResponse:
    """Invite a user through Supabase Auth and mirror the auth uid into public.users."""
    actor_id = require_actor(tenant)
    agency_id = uuid.UUID(tenant.agency_id)
    try:
        invited_id = await supabase.invite_user(email=payload.email)
    except SupabaseAdminError as exc:
        raise AppError("user_invite_failed") from exc
    user = await UserRepository(session, agency_id).upsert_invited_user(
        user_id=invited_id,
        email=payload.email,
        display_name=payload.display_name,
        role=payload.role,
    )
    await audit_writer(tenant, session, request).record(
        actor_id=actor_id,
        action="user.invite",
        resource_type="user",
        resource_id=str(user.id),
        metadata={"role": payload.role.value},
    )
    await session.commit()
    return UserInviteResponse(
        user_id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        agency_id=str(user.agency_id),
    )

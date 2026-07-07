"""Summary: User identity API models for real-auth Track B. The `/me` response is the
frontend's role source after Supabase login, and the admin invite request/response models
cover the provisioning workflow that creates a Supabase Auth user then reconciles the
matching tenant-scoped `users` row. All external fields serialize camelCase while Python
internals stay snake_case.

Key classes:
- CurrentUserResponse: verified caller identity returned by GET /api/v1/me.
- UserInviteRequest: admin-only invite/provision request.
- UserInviteResponse: created/reconciled user row returned by POST /api/v1/users.

Key functions:
- (none)

Notes:
- Role values reuse the canonical UserRole enum, avoiding a parallel frontend/backend role list.
- The response never returns secrets or raw token claims; it reports the DB-backed email plus
  the already-verified tenant/role context.
"""

from __future__ import annotations

from pydantic import Field

from fraudlens_backend.db.models.enums import UserRole
from fraudlens_backend.models.common import CamelModel


class CurrentUserResponse(CamelModel):
    """The authenticated caller identity used by the frontend session store."""

    email: str = Field(..., min_length=3, max_length=320, description="Provisioned email address.")
    role: UserRole = Field(..., description="FraudLens RBAC role enforced for this request.")
    agency_id: str = Field(..., description="Tenant agency id from the verified access token.")


class UserInviteRequest(CamelModel):
    """Admin-only request to invite a user and create its tenant-scoped row."""

    email: str = Field(
        ...,
        min_length=3,
        max_length=320,
        description="Email address to invite through Supabase Auth.",
    )
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable display name stored in the tenant users table.",
    )
    role: UserRole = Field(..., description="FraudLens RBAC role granted to the invited user.")


class UserInviteResponse(CamelModel):
    """The reconciled tenant user returned after a successful invite."""

    user_id: str = Field(..., description="Supabase auth.users id mirrored into public.users.id.")
    email: str = Field(..., min_length=3, max_length=320, description="Provisioned email address.")
    display_name: str = Field(..., description="Stored display name.")
    role: UserRole = Field(..., description="Stored FraudLens RBAC role.")
    agency_id: str = Field(..., description="Tenant agency id assigned by the inviting admin.")

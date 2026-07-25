"""Summary: Idempotently provision the synthetic demo personas in Supabase Auth and mirror their
auth UUIDs into each tenant's `public.users` rows. Covers both demo tenants (`LIVE_DEMO_USERS`):
the primary agency's four roles plus Agency Two's analyst, so the research page's cross-tenant
view can be exercised by signing into a genuinely separate agency. Fixed seed users remain as
history-only actors so existing alert/SAR/training audit foreign keys are never rewritten.

Key classes:
- DemoAuthProvisionSummary: safe counts emitted by the provisioning command.

Key functions:
- provision_demo_auth: ensure Supabase identities and tenant-scoped application users.
- main: run provisioning against the configured non-production live-local environment.

Notes:
- Service-role credentials are read from Infisical-backed settings and never logged or persisted.
- The shared demo password is intentionally public and synthetic; live authorization still uses a
  verified Supabase JWT and the server-owned role on the matching `public.users` row.
"""

from __future__ import annotations

import asyncio
import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import Agency, AuditLog
from fraudlens_backend.db.repositories import UserRepository
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.demo import (
    AML_DEMO_AGENCIES,
    DEMO_AUTH_PASSWORD,
    LIVE_DEMO_USERS,
    DemoUserSpec,
)
from fraudlens_backend.services.supabase_admin import (
    SupabaseAdminClient,
    SupabaseAdminError,
    SupabaseAuthAppMetadata,
)
from fraudlens_backend.settings import AppSettings, get_settings


class DemoAuthProvisionSummary(BaseModel):
    """Safe aggregate result for one idempotent demo-auth provisioning run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    auth_users: int = Field(..., description="Supabase Auth demo users ensured.")
    app_users: int = Field(..., description="Canonical tenant-scoped application users ensured.")
    reconciled_users: int = Field(..., description="Application identities changed this run.")


_AGENCY_BY_ID = {spec.agency_id: spec for spec in AML_DEMO_AGENCIES}


def _history_email(spec: DemoUserSpec) -> str:
    """Return the deterministic non-login email retained by a fixed historical seed actor."""
    return f"seed-{spec.role.value}@{_AGENCY_BY_ID[spec.agency_id].slug}.test"


async def _ensure_agency(session: AsyncSession, agency_id: uuid.UUID) -> None:
    """Ensure one synthetic tenant exists before inserting its auth-backed users."""
    if await session.get(Agency, agency_id) is None:
        spec = _AGENCY_BY_ID[agency_id]
        session.add(Agency(id=spec.agency_id, name=spec.name, slug=spec.slug))
        await session.flush()


async def _reconcile_app_user(
    session: AsyncSession,
    *,
    spec: DemoUserSpec,
    auth_user_id: uuid.UUID,
) -> bool:
    """Mirror one auth UUID into its owning tenant, preserving any fixed seed actor by history."""
    repository = UserRepository(session, spec.agency_id)
    canonical = await repository.get_by_email(spec.email)
    auth_user = await repository.get_by_id(auth_user_id)
    changed = (
        canonical is None
        or canonical.id != auth_user_id
        or auth_user is None
        or auth_user.display_name != spec.display_name
        or auth_user.role != spec.role
    )
    if canonical is not None and canonical.id != auth_user_id:
        canonical.email = _history_email(spec)
        await session.flush()
    user = await repository.upsert_invited_user(
        user_id=auth_user_id,
        email=spec.email,
        display_name=spec.display_name,
        role=spec.role,
    )
    user.display_name = spec.display_name
    user.role = spec.role
    if changed:
        session.add(
            AuditLog(
                agency_id=spec.agency_id,
                actor_id=None,
                action="demo_auth.provision",
                resource_type="user",
                resource_id=str(user.id),
                meta={"role": spec.role.value},
                request_id=f"demo-auth-{spec.agency_id}-{spec.role.value}",
            )
        )
    return changed


async def provision_demo_auth(
    session: AsyncSession,
    client: SupabaseAdminClient,
) -> DemoAuthProvisionSummary:
    """Ensure every demo Auth user and its tenant-scoped application identity row (all tenants)."""
    for agency_id in dict.fromkeys(spec.agency_id for spec in LIVE_DEMO_USERS):
        await _ensure_agency(session, agency_id)
    reconciled = 0
    for spec in LIVE_DEMO_USERS:
        auth_user_id = await client.ensure_password_user(
            email=spec.email,
            password=DEMO_AUTH_PASSWORD,
            app_metadata=SupabaseAuthAppMetadata(
                agency_id=str(spec.agency_id),
                user_role=spec.role.value,
            ),
        )
        if await _reconcile_app_user(session, spec=spec, auth_user_id=auth_user_id):
            reconciled += 1
    await session.flush()
    return DemoAuthProvisionSummary(
        auth_users=len(LIVE_DEMO_USERS),
        app_users=len(LIVE_DEMO_USERS),
        reconciled_users=reconciled,
    )


async def _amain(settings: AppSettings | None = None) -> int:
    """Build live clients and provision only when the app is explicitly non-production."""
    resolved_settings = settings or get_settings()
    if resolved_settings.environment == "prod":
        print("demo auth provisioning refused: application environment must not be prod")
        return 1
    engine = create_engine_from_settings(resolved_settings)
    if engine is None:
        print("demo auth provisioning failed: DATABASE_URL is not configured")
        return 1
    try:
        client = SupabaseAdminClient.from_settings(resolved_settings)
        sessionmaker = build_sessionmaker(engine)
        async with sessionmaker() as session:
            summary = await provision_demo_auth(session, client)
            await session.commit()
    except SupabaseAdminError:
        print("demo auth provisioning failed: Supabase admin request was rejected")
        return 1
    finally:
        await engine.dispose()
    print(
        "demo auth provisioning OK: "
        f"{summary.auth_users} auth users, {summary.app_users} app users, "
        f"{summary.reconciled_users} reconciled"
    )
    return 0


def main() -> int:
    """CLI entry point for live-local demo authentication provisioning."""
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())

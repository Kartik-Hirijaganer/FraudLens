"""Summary: Idempotently provision the CONFIGURED demo personas in Supabase Auth and mirror their
auth UUIDs into the single demo tenant's `public.users` rows. Every identity value — the agency,
the persona emails/roles/display names — comes from `config/portfolio-demo.yaml`, and the shared
synthetic password comes from settings (`FRAUDLENS_DEMO_AUTH_PASSWORD` via Infisical), so this
script holds no demo identity or credential literal. Fixed seed users remain as history-only
actors (at `PortfolioDemoConfig.history_email`, the one derivation the seed shares) so existing
alert/SAR/training audit foreign keys are never rewritten.

Key classes:
- DemoAuthProvisionSummary: safe counts emitted by the provisioning command.

Key functions:
- provision_demo_auth: ensure Supabase identities and tenant-scoped application users.
- main: run provisioning against the configured non-production live-local environment.

Notes:
- Service-role credentials are read from Infisical-backed settings and never logged or persisted.
- The shared demo password is intentionally public and synthetic; live authorization still uses a
  verified Supabase JWT and the server-owned role on the matching `public.users` row. It is still
  supplied through env/Infisical so `make secrets-scan` stays strict.
- Exactly one persistent demo tenant exists (see the portfolio demo story); generic multi-tenancy
  is proven by tests that mint throwaway tenants, never by a second provisioned agency.
"""

from __future__ import annotations

import asyncio
import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import Agency, AuditLog
from fraudlens_backend.db.repositories import UserRepository
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.portfolio_demo import (
    PortfolioDemoConfig,
    PortfolioDemoPersona,
    load_portfolio_demo_config,
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


async def _ensure_agency(session: AsyncSession, config: PortfolioDemoConfig) -> None:
    """Ensure the configured synthetic tenant exists before inserting its auth-backed users."""
    agency = config.agency
    if await session.get(Agency, agency.id) is None:
        session.add(Agency(id=agency.id, name=agency.name, slug=agency.slug))
        await session.flush()


async def _reconcile_app_user(
    session: AsyncSession,
    *,
    persona: PortfolioDemoPersona,
    config: PortfolioDemoConfig,
    auth_user_id: uuid.UUID,
) -> bool:
    """Mirror one auth UUID into the demo tenant, preserving any fixed seed actor by history."""
    repository = UserRepository(session, config.agency.id)
    canonical = await repository.get_by_email(persona.email)
    auth_user = await repository.get_by_id(auth_user_id)
    changed = (
        canonical is None
        or canonical.id != auth_user_id
        or auth_user is None
        or auth_user.display_name != persona.display_name
        or auth_user.role != persona.role
    )
    if canonical is not None and canonical.id != auth_user_id:
        canonical.email = config.history_email(persona)
        await session.flush()
    user = await repository.upsert_invited_user(
        user_id=auth_user_id,
        email=persona.email,
        display_name=persona.display_name,
        role=persona.role,
    )
    user.display_name = persona.display_name
    user.role = persona.role
    if changed:
        session.add(
            AuditLog(
                agency_id=config.agency.id,
                actor_id=None,
                action="demo_auth.provision",
                resource_type="user",
                resource_id=str(user.id),
                meta={"role": persona.role.value},
                request_id=f"demo-auth-{config.story_identity}-{persona.key}",
            )
        )
    return changed


async def provision_demo_auth(
    session: AsyncSession,
    client: SupabaseAdminClient,
    *,
    password: str,
    config: PortfolioDemoConfig | None = None,
) -> DemoAuthProvisionSummary:
    """Ensure every configured Auth user and its tenant-scoped application identity row."""
    resolved = config or load_portfolio_demo_config()
    await _ensure_agency(session, resolved)
    reconciled = 0
    for persona in resolved.personas:
        auth_user_id = await client.ensure_password_user(
            email=persona.email,
            password=password,
            app_metadata=SupabaseAuthAppMetadata(
                agency_id=str(resolved.agency.id),
                user_role=persona.role.value,
            ),
        )
        if await _reconcile_app_user(
            session, persona=persona, config=resolved, auth_user_id=auth_user_id
        ):
            reconciled += 1
    await session.flush()
    return DemoAuthProvisionSummary(
        auth_users=len(resolved.personas),
        app_users=len(resolved.personas),
        reconciled_users=reconciled,
    )


async def _amain(settings: AppSettings | None = None) -> int:
    """Build live clients and provision only when the app is explicitly non-production."""
    resolved_settings = settings or get_settings()
    if resolved_settings.environment == "prod":
        print("demo auth provisioning refused: application environment must not be prod")
        return 1
    if not resolved_settings.demo_auth_password:
        print("demo auth provisioning failed: FRAUDLENS_DEMO_AUTH_PASSWORD is not configured")
        return 1
    engine = create_engine_from_settings(resolved_settings)
    if engine is None:
        print("demo auth provisioning failed: DATABASE_URL is not configured")
        return 1
    try:
        client = SupabaseAdminClient.from_settings(resolved_settings)
        sessionmaker = build_sessionmaker(engine)
        async with sessionmaker() as session:
            summary = await provision_demo_auth(
                session,
                client,
                password=resolved_settings.demo_auth_password,
                config=load_portfolio_demo_config(settings=resolved_settings),
            )
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

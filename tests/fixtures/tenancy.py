"""Throwaway tenant and user factories for the behavioral suite.

Generic behavior — a repository, an API route, a job, an alert transition — is tenant-GENERIC and
must be provable without the portfolio demo story. Before these factories, a dozen suites each
restated the configured demo agency's UUID, which made them silently depend on
`config/portfolio-demo.yaml` and would fail `make demo-literals-check`. Every helper here mints a
fresh random id instead, so two calls never collide and a test can hold two tenants at once and
watch one fail to see the other's rows.

Never register a tenant minted here in runtime configuration: exactly one persistent portfolio
agency exists (ADR-018). Tests that genuinely exercise the CONFIGURED identity — the dev bypass,
the foundation seed, the bootstrap — read it from `portfolio_demo_identity` instead.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import Agency, User, UserRole


def new_agency_id() -> uuid.UUID:
    """Return a throwaway tenant id (no DB row); use when only the scope value is needed."""
    return uuid.uuid4()


def new_user_id() -> uuid.UUID:
    """Return a throwaway actor id (no DB row); use for an actor/claim value with no row."""
    return uuid.uuid4()


def build_agency(*, label: str = "tenant") -> Agency:
    """Build (without adding) an Agency whose name and slug are unique to this call."""
    scope = uuid.uuid4().hex[:12]
    return Agency(id=uuid.uuid4(), name=f"Test {label} {scope}", slug=f"{label}-{scope}")


def build_user(
    *, agency_id: uuid.UUID, role: UserRole = UserRole.ANALYST, label: str = "user"
) -> User:
    """Build (without adding) a User in `agency_id` whose email is unique to this call.

    `users.email` is globally unique, so the random scope matters even across tenants.
    """
    scope = uuid.uuid4().hex[:12]
    return User(
        id=uuid.uuid4(),
        agency_id=agency_id,
        email=f"{label}-{scope}@tenant.test",
        display_name=f"Test {label.title()} {scope}",
        role=role,
    )


async def create_agency(session: AsyncSession, *, label: str = "tenant") -> Agency:
    """Insert and flush a throwaway tenant, returning the persisted row."""
    agency = build_agency(label=label)
    session.add(agency)
    await session.flush()
    return agency


async def create_user(
    session: AsyncSession,
    *,
    agency_id: uuid.UUID,
    role: UserRole = UserRole.ANALYST,
    label: str = "user",
) -> User:
    """Insert and flush a throwaway user in `agency_id`, returning the persisted row."""
    user = build_user(agency_id=agency_id, role=role, label=label)
    session.add(user)
    await session.flush()
    return user


async def create_tenant(
    session: AsyncSession, *, label: str = "tenant", role: UserRole = UserRole.ANALYST
) -> tuple[Agency, User]:
    """Insert a throwaway tenant together with one user in it (the common two-line setup)."""
    agency = await create_agency(session, label=label)
    user = await create_user(session, agency_id=agency.id, role=role, label=label)
    return agency, user

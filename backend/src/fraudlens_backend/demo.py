"""Summary: Canonical demo-tenant identity, shared by the auth dev-bypass and the seed so
they agree on one tenant (plan §3.4). The dev bypass (api/deps.py) mints claims for
`DEMO_AGENCY_ID`, and `scripts/seed.py` creates exactly that agency plus `DEMO_USERS`, so in
`make local-demo` the bypassed identity resolves to a real, seeded agency end-to-end. The
ids/emails are obviously-synthetic placeholders (no real PHI/tenant), matching the
tests/fixtures policy.

Key classes:
- DemoAgencySpec: a deterministic synthetic agency used by real-dataset demo ingestion.
- DemoUserSpec: a seeded demo user's email, display name, and role.

Key functions:
- (none)

Notes:
- `DEMO_AGENCY_ID` / `DEMO_USER_ID` are fixed UUIDs (not random) so the dev bypass, the seed,
  and tests all reference the same tenant + default acting user deterministically across runs.
- `DEMO_AUTH_PASSWORD` is an intentionally public synthetic credential used only by local demo
  tooling; it is not an application secret and never grants access outside the demo tenant.
- This module holds identity constants ONLY; the rest of the seed payload (config, fixture
  model) lives in `scripts/seed.py` so the runtime image carries no demo data.
- `DEMO_USERS` are the primary tenant's roles (seeded + dev-bypass reachable); `LIVE_DEMO_USERS`
  adds the second tenant's analyst, provisioned only into live Supabase auth (agency-bound JWT)
  so the research page's two-tenant view is demonstrable end to end.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_backend.db.models.enums import UserRole

# Fixed demo tenant id — the dev bypass and the seed both reference exactly this agency.
DEMO_AGENCY_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
DEMO_AGENCY_NAME = "Demo Financial Agency"
DEMO_AGENCY_SLUG = "demo-agency"
DEMO_AUTH_PASSWORD = "demo-access-2026"


class DemoAgencySpec(BaseModel):
    """A deterministic synthetic agency used to partition public AML demo rows."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agency_id: uuid.UUID = Field(..., description="Fixed tenant id used for deterministic scope.")
    name: str = Field(..., description="Synthetic display name (never a source institution).")
    slug: str = Field(..., description="Stable unique slug for the agency row.")


AML_DEMO_AGENCIES: tuple[DemoAgencySpec, ...] = (
    DemoAgencySpec(
        agency_id=DEMO_AGENCY_ID,
        name=DEMO_AGENCY_NAME,
        slug=DEMO_AGENCY_SLUG,
    ),
    DemoAgencySpec(
        agency_id=uuid.UUID("11111111-1111-4111-8111-111111111112"),
        name="AML Demo Agency Two",
        slug="aml-demo-agency-two",
    ),
    DemoAgencySpec(
        agency_id=uuid.UUID("11111111-1111-4111-8111-111111111113"),
        name="AML Demo Agency Three",
        slug="aml-demo-agency-three",
    ),
)

# Fixed acting-user id the dev bypass mints (the demo analyst). The seed creates exactly this
# user, so a bypassed identity resolves to a real `users` row — the actor every audited Phase 9
# action (`alert_actions.actor_id`, `training_labels.created_by`) is recorded under.
DEMO_USER_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


class DemoUserSpec(BaseModel):
    """A seeded demo user's identity (fixed id, email, display name, role, owning tenant)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: uuid.UUID = Field(..., description="Fixed synthetic demo user id (deterministic).")
    email: str = Field(..., description="Synthetic demo login email (no real identity).")
    display_name: str = Field(..., description="Human-readable demo user name.")
    role: UserRole = Field(..., description="RBAC role granted to the demo user.")
    agency_id: uuid.UUID = Field(
        default=DEMO_AGENCY_ID,
        description="Owning tenant id; defaults to the primary demo agency.",
    )


# The demo agency's users: one of each role, seeded idempotently by (agency_id, email). The
# analyst carries the fixed DEMO_USER_ID so the no-header dev-bypass acting user maps to a real row.
DEMO_USERS: tuple[DemoUserSpec, ...] = (
    DemoUserSpec(
        user_id=uuid.UUID("55555555-5555-4555-8555-555555555555"),
        email="auditor@demo-agency.test",
        display_name="Demo Auditor",
        role=UserRole.AUDITOR,
    ),
    DemoUserSpec(
        user_id=DEMO_USER_ID,
        email="analyst@demo-agency.test",
        display_name="Demo Analyst",
        role=UserRole.ANALYST,
    ),
    DemoUserSpec(
        user_id=uuid.UUID("33333333-3333-4333-8333-333333333333"),
        email="reviewer@demo-agency.test",
        display_name="Demo Reviewer",
        role=UserRole.REVIEWER,
    ),
    DemoUserSpec(
        user_id=uuid.UUID("44444444-4444-4444-8444-444444444444"),
        email="admin@demo-agency.test",
        display_name="Demo Admin",
        role=UserRole.ADMIN,
    ),
)

# The second isolated demo tenant's analyst. Unlike DEMO_USERS (seeded into the primary tenant and
# reachable through the tokenless dev bypass), this identity is provisioned ONLY into live Supabase
# auth by scripts/provision_demo_auth.py, so the research page's cross-tenant view can be
# demonstrated by signing into a genuinely separate agency. Never seeded into the offline demo.
AML_DEMO_AGENCY_TWO_ID = AML_DEMO_AGENCIES[1].agency_id

LIVE_DEMO_USERS: tuple[DemoUserSpec, ...] = (
    *DEMO_USERS,
    DemoUserSpec(
        user_id=uuid.UUID("66666666-6666-4666-8666-666666666666"),
        email="analyst@aml-demo-agency-two.test",
        display_name="Sam Okafor",
        role=UserRole.ANALYST,
        agency_id=AML_DEMO_AGENCY_TWO_ID,
    ),
)

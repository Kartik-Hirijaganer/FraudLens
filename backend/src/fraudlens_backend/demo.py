"""Summary: Canonical demo-tenant identity, shared by the auth dev-bypass and the seed so
they agree on one tenant (plan §3.4). The dev bypass (api/deps.py) mints claims for
`DEMO_AGENCY_ID`, and `scripts/seed.py` creates exactly that agency plus `DEMO_USERS`, so in
`make local-demo` the bypassed identity resolves to a real, seeded agency end-to-end. The
ids/emails are obviously-synthetic placeholders (no real PHI/tenant), matching the
tests/fixtures policy.

Key classes:
- DemoUserSpec: a seeded demo user's email, display name, and role.

Key functions:
- (none)

Notes:
- `DEMO_AGENCY_ID` is a fixed UUID (not random) so the dev bypass, the seed, and tests all
  reference the same tenant deterministically across runs.
- This module holds identity constants ONLY; the rest of the seed payload (config, fixture
  model) lives in `scripts/seed.py` so the runtime image carries no demo data.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_backend.db.models.enums import UserRole

# Fixed demo tenant id — the dev bypass and the seed both reference exactly this agency.
DEMO_AGENCY_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
DEMO_AGENCY_NAME = "Demo Financial Agency"
DEMO_AGENCY_SLUG = "demo-agency"


class DemoUserSpec(BaseModel):
    """A seeded demo user's identity (email, display name, role)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    email: str = Field(..., description="Synthetic demo login email (no real identity).")
    display_name: str = Field(..., description="Human-readable demo user name.")
    role: UserRole = Field(..., description="RBAC role granted to the demo user.")


# The demo agency's users: one of each role, seeded idempotently by (agency_id, email).
DEMO_USERS: tuple[DemoUserSpec, ...] = (
    DemoUserSpec(
        email="analyst@demo-agency.test", display_name="Demo Analyst", role=UserRole.ANALYST
    ),
    DemoUserSpec(
        email="reviewer@demo-agency.test", display_name="Demo Reviewer", role=UserRole.REVIEWER
    ),
    DemoUserSpec(email="admin@demo-agency.test", display_name="Demo Admin", role=UserRole.ADMIN),
)

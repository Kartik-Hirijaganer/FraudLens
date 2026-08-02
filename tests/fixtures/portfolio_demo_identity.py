"""The configured portfolio-demo identity, resolved once for the tests that exercise it.

Tests that drive the non-prod dev bypass (or the foundation seed) must agree with whatever
`config/portfolio-demo.yaml` declares, so they read it here instead of restating ids. Generic
behavior tests should stay independent of this file and mint their own throwaway tenants.
"""

from __future__ import annotations

import uuid

from fraudlens_backend.db.models.enums import UserRole
from fraudlens_backend.portfolio_demo import load_portfolio_demo_config
from fraudlens_backend.settings import AppSettings

_CONFIG = load_portfolio_demo_config()

DEMO_AGENCY_ID = _CONFIG.agency.id
DEMO_AGENCY_NAME = _CONFIG.agency.name
DEMO_AGENCY_SLUG = _CONFIG.agency.slug
DEMO_PERSONAS = _CONFIG.personas


def demo_user_id(role: UserRole) -> uuid.UUID:
    """Return the configured persona id for a role (KeyError-free: roles are all configured)."""
    persona = _CONFIG.persona_for_role(role)
    if persona is None:  # pragma: no cover - the committed story configures every role
        raise LookupError(f"no configured portfolio demo persona for role '{role.value}'")
    return persona.seed_user_id


def demo_history_email(role: UserRole) -> str:
    """Return the history-only email a displaced fixed seed actor is addressed by.

    Delegates to the config so a test can never assert a derivation the seed and the provisioning
    script do not actually share.
    """
    persona = next(spec for spec in DEMO_PERSONAS if spec.role is role)
    return _CONFIG.history_email(persona)


# The acting user the tokenless dev bypass mints with no demo-role header: the persona holding
# the configured `auth_dev_bypass_role`.
DEMO_BYPASS_USER_ID = demo_user_id(UserRole(AppSettings().auth_dev_bypass_role))

# The seeded analyst — the ordinary actor for `created_by` / `approved_by` in suites that run the
# foundation seed and therefore need a user row that actually exists in the demo tenant.
DEMO_ANALYST_ID = demo_user_id(UserRole.ANALYST)

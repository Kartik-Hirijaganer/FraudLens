"""Summary: The SAFE PUBLIC PROJECTION of `config/portfolio-demo.yaml` (plan Phase 3a). The
login picker needs persona presentation metadata BEFORE anyone is authenticated, and the
frontend must not duplicate the YAML in TypeScript, so these `CamelModel`s carry the one subset
of the story that is deliberately public: the story version, the single runtime agency's public
identity, each persona's login/presentation fields, and the public synthetic demo password.
Everything else in the story document — seeded user ids, authored transactions, expected
outcomes, workflow review notes, model/artifact provenance, and any provider or database setting
— is absent BY CONSTRUCTION: the projection is an allowlist of named fields, not a dump of the
validated config, so a new YAML key cannot leak by default.

Key classes:
- PortfolioDemoAgencyView: the demo tenant's public identity (+ its offline research partition).
- PortfolioDemoPersonaView: one selectable login persona as the picker renders it.
- PortfolioDemoConfigResponse: the whole public projection returned to the pre-auth screen.

Key functions:
- (none)

Notes:
- `synthetic_password` is intentionally public: it is non-secret synthetic demo data whose value
  still resolves from env/Infisical (`AppSettings.demo_auth_password`) rather than a committed
  literal, so `make secrets-scan` stays strict. It is EMPTY when unconfigured, which degrades the
  picker's auto-fill instead of failing the screen.
- `picker_accent` reuses the code-owned accent token union, so `DESIGN.md`'s palette rules cannot
  be violated from YAML — config only SELECTS a token that code already permits.
- The persona's `key` is the config key (a stable label), never the seeded `users.id`.
"""

from __future__ import annotations

from pydantic import Field

from fraudlens_backend.db.models.enums import UserRole
from fraudlens_backend.models.common import CamelModel
from fraudlens_backend.portfolio_demo import PortfolioDemoAccent


class PortfolioDemoAgencyView(CamelModel):
    """The single runtime demo tenant, as the pre-auth login screen may know it."""

    id: str = Field(..., description="The demo agency id the picked persona signs in against.")
    name: str = Field(..., description="Synthetic display name of the demo agency.")
    slug: str = Field(..., description="Stable URL-safe slug of the demo agency.")
    research_partition_key: str = Field(
        ...,
        description=(
            "Name of the OFFLINE study partition this tenant mirrors in the committed GFP "
            "artifact; an analysis concept, never a second runtime tenant."
        ),
    )


class PortfolioDemoPersonaView(CamelModel):
    """One selectable demo login persona, with only what the picker and shell render."""

    key: str = Field(..., description="Stable config key identifying the persona.")
    role: UserRole = Field(..., description="RBAC role the persona signs in with.")
    email: str = Field(..., description="Synthetic login email the picker auto-fills.")
    display_name: str = Field(..., description="Display identity shown in the signed-in shell.")
    initials: str = Field(..., description="Avatar initials shown in the signed-in shell.")
    picker_name: str = Field(..., description="Label the login picker lists the persona under.")
    picker_tag: str = Field(..., description="Short picker tag (e.g. 'Queue').")
    picker_accent: PortfolioDemoAccent = Field(
        ..., description="Semantic accent token colouring the picker dot (code owns the set)."
    )


class PortfolioDemoConfigResponse(CamelModel):
    """The public portfolio-demo projection consumed by the unauthenticated login screen."""

    story_version: str = Field(
        ..., description="Story revision the running demo state was built from."
    )
    agency: PortfolioDemoAgencyView = Field(..., description="The single runtime demo tenant.")
    personas: list[PortfolioDemoPersonaView] = Field(
        ..., description="Selectable demo personas, in configured order."
    )
    synthetic_password: str = Field(
        ...,
        description=(
            "The deliberately public synthetic demo password the picker auto-fills; empty when "
            "no credential is configured, which disables auto-fill rather than the screen."
        ),
    )

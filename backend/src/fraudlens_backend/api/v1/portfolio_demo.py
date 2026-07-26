"""Summary: The unauthenticated portfolio-demo projection route (plan Phase 3a).
`GET /portfolio-demo/config` hands the pre-auth login screen the safe public subset of
`config/portfolio-demo.yaml` so the persona picker renders from the backend's validated story
instead of duplicating it in TypeScript (rule 5). It is deliberately tokenless — the screen that
calls it has no session yet — and 404s unless the portfolio demo is enabled or the non-prod dev
bypass is on, so a hardened deployment exposes no demo surface at all. Its edge policy (no
required role, its own request budget) is declared in `config/gateway/routes.yaml`.

Key classes:
- (none)

Key functions:
- read_portfolio_demo_config: GET /portfolio-demo/config — the public story projection, or 404.

Notes:
- The returned `syntheticPassword` is PUBLIC BY DESIGN: it is non-secret synthetic demo data for
  a portfolio walkthrough, and its value still comes from env/Infisical
  (`AppSettings.demo_auth_password`) rather than a committed literal. It authenticates only the
  seeded synthetic demo identities in the single demo tenant; no real credential is exposed.
- The response model is an explicit allowlist (`models/portfolio_demo.py`), so seeded user ids,
  authored transactions, expected outcomes, workflow notes, model provenance, and provider or
  database settings are never serialized.
- No tenant dependency: the route returns configuration, touches no database, and accepts no
  client-supplied tenant id, so tenant isolation is unaffected.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from fraudlens_backend.api.deps import SettingsDep
from fraudlens_backend.models.portfolio_demo import (
    PortfolioDemoAgencyView,
    PortfolioDemoConfigResponse,
    PortfolioDemoPersonaView,
)
from fraudlens_backend.portfolio_demo import load_portfolio_demo_config
from fraudlens_backend.settings import AppSettings

router = APIRouter(tags=["portfolio-demo"])


def _projection_enabled(settings: AppSettings) -> bool:
    """True when the demo surface may be advertised: portfolio mode, or the non-prod bypass.

    `is_dev_bypass_enabled` is already False in prod regardless of its flag, so the only way to
    expose this route in production is the explicit `portfolio_demo_enabled` gate, which itself
    defaults to False in code (fail closed).
    """
    return settings.portfolio_demo_enabled or settings.is_dev_bypass_enabled


@router.get("/portfolio-demo/config", response_model=PortfolioDemoConfigResponse)
async def read_portfolio_demo_config(settings: SettingsDep) -> PortfolioDemoConfigResponse:
    """Return the public portfolio-demo projection; 404 when the demo surface is disabled."""
    if not _projection_enabled(settings):
        raise HTTPException(status_code=404, detail="portfolio demo is not enabled")
    config = load_portfolio_demo_config(settings=settings)
    return PortfolioDemoConfigResponse(
        story_version=config.story_version,
        agency=PortfolioDemoAgencyView(
            id=str(config.agency.id),
            name=config.agency.name,
            slug=config.agency.slug,
            research_partition_key=config.agency.research_partition_key,
        ),
        personas=[
            PortfolioDemoPersonaView(
                key=persona.key,
                role=persona.role,
                email=persona.email,
                display_name=persona.display_name,
                initials=persona.initials,
                picker_name=persona.picker_name,
                picker_tag=persona.picker_tag,
                picker_accent=persona.picker_accent,
            )
            for persona in config.personas
        ],
        synthetic_password=settings.demo_auth_password or "",
    )

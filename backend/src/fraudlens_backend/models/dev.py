"""Summary: Response models for non-production developer utility endpoints. The surface reports
which utility was accepted, its status, and the audited tenant scope. Every route is disabled in
production and remains admin-gated even in development.

Key classes:
- DevUtilityResponse: standard response for governed non-production utility mutations.

Key functions:
- (none)

Notes:
- These endpoints coordinate local/demo workflows; deploy-time seeding still uses scripts/seed.py.
"""

from __future__ import annotations

from pydantic import Field

from fraudlens_backend.models.common import CamelModel


class DevUtilityResponse(CamelModel):
    """Acknowledgement for a non-production dev utility action."""

    action: str = Field(..., description="Utility action accepted by the API.")
    status: str = Field(..., description="Outcome status for the utility request.")
    agency_id: str = Field(..., description="Tenant scope under which the action was audited.")

"""Summary: The business-surface heartbeat at GET /api/v1/health. Unlike the
unprefixed ops probes (/healthz, /readyz), this lives under the versioned /api/v1
prefix and confirms the public API surface is reachable, reporting the service
name, version, and active environment. It requires no authentication so smoke
tests and uptime checks can call it.

Key classes:
- ApiHealthResponse: the camelCase body returned by /api/v1/health.

Key functions:
- api_health: handler returning the API-surface heartbeat.

Notes:
- Response is a CamelModel, so fields serialize as camelCase per the FraudLens casing rule.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import Field

from fraudlens_backend import __version__
from fraudlens_backend.api.deps import SettingsDep
from fraudlens_backend.models.common import CamelModel

router = APIRouter(tags=["health"])


class ApiHealthResponse(CamelModel):
    """Heartbeat payload for the versioned API surface."""

    status: str = Field(..., description="'ok' when the API surface is serving.")
    service: str = Field(..., description="Service name from settings.")
    version: str = Field(..., description="Running service version.")
    environment: str = Field(..., description="Active environment (dev/prod).")


@router.get("/health", response_model=ApiHealthResponse)
async def api_health(settings: SettingsDep) -> ApiHealthResponse:
    """Return the API-surface heartbeat (status, service, version, environment)."""
    return ApiHealthResponse(
        status="ok",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
    )

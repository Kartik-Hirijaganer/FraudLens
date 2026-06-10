"""Summary: Operational endpoints used by the deploy platform and smoke tests.
GET /healthz is liveness (the process is up). GET /readyz is readiness: it runs a
set of dependency probes (database / ChromaDB / Infisical) and returns 200 only
when none report "down", else 503. Both are UNPREFIXED (no /api/v1) per the
endpoint contract. The probes are pluggable via a dependency so real reachability
checks can be wired in later (and so tests can simulate a degraded dependency);
in this skeleton they report "skipped" because those services are not provisioned.

Key classes:
- LivenessResponse: body of /healthz.
- DependencyCheck: one dependency's readiness result.
- ReadinessResponse: body of /readyz (overall status + per-dependency checks).

Key functions:
- default_readiness_probes: the skeleton's (skipped) dependency probes.
- get_readiness_probes: dependency returning the active probe set (overridable).
- healthz: liveness handler.
- readyz: readiness handler returning 200/503 from the aggregate.

Notes:
- /readyz sets the HTTP status from the aggregate so platform probes can gate on it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import Field
from starlette.responses import Response

from fraudlens_backend.models.common import CamelModel

router = APIRouter(tags=["ops"])

ReadinessProbe = Callable[[], "DependencyCheck"]


class LivenessResponse(CamelModel):
    """Liveness payload — the process is running and serving."""

    status: str = Field(default="ok", description="Always 'ok' when the process serves.")


class DependencyCheck(CamelModel):
    """Readiness result for a single downstream dependency."""

    name: str = Field(..., description="Dependency name, e.g. 'database'.")
    status: str = Field(..., description="One of 'ok', 'down', or 'skipped'.")
    detail: str | None = Field(default=None, description="Optional, PHI-free detail.")


class ReadinessResponse(CamelModel):
    """Aggregate readiness — overall status plus each dependency's check."""

    status: str = Field(..., description="'ready' when no dependency is 'down'.")
    checks: list[DependencyCheck] = Field(..., description="Per-dependency results.")


def _skipped(name: str) -> DependencyCheck:
    """Return a 'skipped' check for a dependency that is not provisioned yet."""
    return DependencyCheck(name=name, status="skipped", detail="not configured in skeleton")


def default_readiness_probes() -> list[ReadinessProbe]:
    """Return the skeleton's dependency probes (database, ChromaDB, Infisical)."""
    return [
        lambda: _skipped("database"),
        lambda: _skipped("chromadb"),
        lambda: _skipped("infisical"),
    ]


def get_readiness_probes() -> list[ReadinessProbe]:
    """Dependency yielding the active readiness probes (overridable in tests/wiring)."""
    return default_readiness_probes()


ProbesDep = Annotated[list[ReadinessProbe], Depends(get_readiness_probes)]


@router.get("/healthz", response_model=LivenessResponse)
async def healthz() -> LivenessResponse:
    """Liveness probe — 200 while the process is serving."""
    return LivenessResponse()


@router.get("/readyz", response_model=ReadinessResponse)
async def readyz(response: Response, probes: ProbesDep) -> ReadinessResponse:
    """Readiness probe — 200 when every dependency check is non-'down', else 503."""
    checks = [probe() for probe in probes]
    ready = all(check.status != "down" for check in checks)
    response.status_code = 200 if ready else 503
    return ReadinessResponse(status="ready" if ready else "not_ready", checks=checks)

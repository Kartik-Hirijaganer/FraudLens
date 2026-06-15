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
- get_readiness_probes: dependency building the active probe set from app state.
- healthz: liveness handler.
- readyz: readiness handler returning 200/503 from the aggregate.

Notes:
- /readyz sets the HTTP status from the aggregate so platform probes can gate on it.
- The database probe runs a bounded SELECT 1 against the engine on app.state; when no
  DATABASE_URL is configured it reports "skipped" (the app still boots).
- The ChromaDB probe checks the baked RAG index for presence (plan §16 Phase 6): a populated
  index → "ok"; a missing/empty index → "down" when `rag_index_required` (prod bakes the
  index) else "skipped" (dev/local need not have built it yet). Infisical remains "skipped".
- Probes may be sync or async; readyz awaits any awaitable result.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Annotated, cast

from fastapi import APIRouter, Depends
from pydantic import Field
from starlette.requests import Request
from starlette.responses import Response

from fraudlens_backend.db.session import ping_database
from fraudlens_backend.models.common import CamelModel
from fraudlens_backend.settings import AppSettings

router = APIRouter(tags=["ops"])

ReadinessProbe = Callable[[], "DependencyCheck | Awaitable[DependencyCheck]"]


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
    """Return a 'skipped' check for a dependency that is not configured/provisioned."""
    return DependencyCheck(name=name, status="skipped", detail="not configured")


def get_readiness_probes(request: Request) -> list[ReadinessProbe]:
    """Build the active readiness probes from app state (overridable in tests/wiring)."""
    settings = cast(AppSettings, request.app.state.settings)
    engine = getattr(request.app.state, "db_engine", None)
    rag_index_dir = getattr(request.app.state, "rag_index_dir", None)
    timeout = settings.db_connect_timeout_seconds

    async def _database() -> DependencyCheck:
        """Probe DB connectivity; skipped when unconfigured, down on any failure."""
        if engine is None:
            return _skipped("database")
        try:
            await ping_database(engine, timeout_seconds=timeout)
        except Exception:  # any connectivity failure → down; detail stays PHI-free
            return DependencyCheck(name="database", status="down", detail="unreachable")
        return DependencyCheck(name="database", status="ok")

    def _chromadb() -> DependencyCheck:
        """Probe the baked RAG index presence; down only when an index is required (prod)."""
        if rag_index_dir is None:
            return _skipped("chromadb")
        # Lazy import keeps heavy chromadb out of the import graph until /readyz needs it.
        from fraudlens_ml.rag import index_status  # noqa: PLC0415

        status = index_status(rag_index_dir, settings.rag_collection)
        if status == "ready":
            return DependencyCheck(name="chromadb", status="ok")
        if settings.rag_index_required:
            return DependencyCheck(name="chromadb", status="down", detail=f"index {status}")
        return DependencyCheck(name="chromadb", status="skipped", detail=f"index {status}")

    return [_database, _chromadb, lambda: _skipped("infisical")]


ProbesDep = Annotated[list[ReadinessProbe], Depends(get_readiness_probes)]


@router.get("/healthz", response_model=LivenessResponse)
async def healthz() -> LivenessResponse:
    """Liveness probe — 200 while the process is serving."""
    return LivenessResponse()


@router.get("/readyz", response_model=ReadinessResponse)
async def readyz(response: Response, probes: ProbesDep) -> ReadinessResponse:
    """Readiness probe — 200 when every dependency check is non-'down', else 503."""
    checks: list[DependencyCheck] = []
    for probe in probes:
        result = probe()
        checks.append(await result if inspect.isawaitable(result) else result)
    ready = all(check.status != "down" for check in checks)
    response.status_code = 200 if ready else 503
    return ReadinessResponse(status="ready" if ready else "not_ready", checks=checks)

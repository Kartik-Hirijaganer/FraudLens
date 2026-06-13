"""Summary: Aggregates the versioned /api/v1 business surface. It mounts the health,
transaction-ingestion, AML-rules, model-registry, and telemetry sub-routers, and serves the
tenant-scoped GET /api/v1/agencies/{agency_id} lookup, which exercises the full access path:
fail-closed authentication, agency_id claim validation (401 unauthenticated, 403 tenant
mismatch), then a database existence check — the agency is loaded via AgencyRepository and
a missing row returns 404 (no existence leak). The `tenant` dependency is resolved before
the DB session so an auth or tenancy failure short-circuits before any database access. The
prefix itself is applied by the app factory from settings.api_v1_prefix, so this router is
prefix-agnostic.

Key classes:
- (none)

Key functions:
- read_agency: tenant-scoped DB-backed lookup returning the requested agency (or 404).

Notes:
- Every tenant-scoped resource MUST depend on get_tenant_for_path (or equivalent)
  so a client-supplied agency id is validated against the token claim, never trusted.
- Returns 503 when no database is configured (get_db_session fails closed), but only
  after authentication/tenancy have passed (dependency order: tenant, then session).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from fraudlens_backend.api.deps import DbSessionDep, get_tenant_for_path
from fraudlens_backend.api.v1 import health, model_versions, rules, telemetry, transactions
from fraudlens_backend.db.repositories import AgencyRepository
from fraudlens_backend.models.common import AgencyResponse, TenantContext

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(transactions.router)
api_router.include_router(rules.router)
api_router.include_router(model_versions.router)
api_router.include_router(telemetry.router)

TenantDep = Annotated[TenantContext, Depends(get_tenant_for_path)]


@api_router.get("/agencies/{agency_id}", response_model=AgencyResponse, tags=["tenancy"])
async def read_agency(tenant: TenantDep, session: DbSessionDep) -> AgencyResponse:
    """Return the requested agency once tenancy is validated; 404 if it does not exist."""
    agency = await AgencyRepository(session).get(tenant.agency_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="agency not found")
    return AgencyResponse(agency_id=str(agency.id), name=agency.name, slug=agency.slug)

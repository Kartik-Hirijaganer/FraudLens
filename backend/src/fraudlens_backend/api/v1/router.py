"""Summary: Aggregates the versioned /api/v1 business surface. It mounts the
health sub-router and defines the tenant-scoped demonstrator
GET /api/v1/agencies/{agency_id}, which exercises the full Aegis access path:
fail-closed authentication plus agency_id claim validation (401 when
unauthenticated, 403 on a tenant mismatch, 200 when the claim matches the path).
The prefix itself is applied by the app factory from settings.api_v1_prefix, so
this router is prefix-agnostic.

Key classes:
- (none)

Key functions:
- read_agency: tenant-scoped endpoint returning the caller's validated TenantContext.

Notes:
- Every tenant-scoped resource MUST depend on get_tenant_for_path (or equivalent)
  so a client-supplied agency id is validated against the token claim, never trusted.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from fraudlens_backend.api.deps import get_tenant_for_path
from fraudlens_backend.api.v1 import health
from fraudlens_backend.models.common import TenantContext

api_router = APIRouter()
api_router.include_router(health.router)

TenantDep = Annotated[TenantContext, Depends(get_tenant_for_path)]


@api_router.get("/agencies/{agency_id}", response_model=TenantContext, tags=["tenancy"])
async def read_agency(tenant: TenantDep) -> TenantContext:
    """Return the validated TenantContext for the requested agency (tenant-scoped)."""
    return tenant

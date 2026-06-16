"""Summary: Admin runtime-configuration API for `system_config`. It lists the global defaults
plus the caller agency's overrides and upserts a single global or tenant-scoped key. The surface is
admin-only, tenant-aware, and audited; audit metadata includes only key/scope, never config values,
so the route does not become a side channel for secrets or PHI.

Key classes:
- (none)

Key functions:
- list_config: GET /config — global + caller-agency config rows.
- patch_config: PATCH /config — create/update one global or agency-scoped key.

Notes:
- Boot-critical and secret config remains in YAML/env/Infisical. This route is only for runtime
  tunables already modeled in `system_config` (thresholds, gates, label windows).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.api.deps import (
    DbSessionDep,
    audit_writer,
    get_admin_tenant,
    require_actor,
)
from fraudlens_backend.db.models import SystemConfig
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.config import (
    ConfigEntry,
    ConfigListResponse,
    ConfigPatchRequest,
    ConfigPatchResponse,
)

router = APIRouter(tags=["config"])

AdminDep = Annotated[TenantContext, Depends(get_admin_tenant)]


def _to_entry(row: SystemConfig) -> ConfigEntry:
    """Project a `system_config` row onto the admin API response."""
    return ConfigEntry(
        config_id=str(row.id),
        key=row.key,
        value=row.value,
        agency_id=str(row.agency_id) if row.agency_id is not None else None,
        updated_at=row.updated_at,
    )


def _scope_id(tenant: TenantContext, *, agency_scoped: bool) -> uuid.UUID | None:
    """Return the target agency scope for a config update; None means global."""
    return uuid.UUID(tenant.agency_id) if agency_scoped else None


async def _get_config_row(
    session: AsyncSession, *, agency_id: uuid.UUID | None, key: str
) -> SystemConfig | None:
    """Load one config row, handling NULL global scope explicitly."""
    stmt = select(SystemConfig).where(SystemConfig.key == key)
    if agency_id is None:
        stmt = stmt.where(SystemConfig.agency_id.is_(None))
    else:
        stmt = stmt.where(SystemConfig.agency_id == agency_id)
    return (await session.execute(stmt)).scalar_one_or_none()


@router.get("/config", response_model=ConfigListResponse)
async def list_config(tenant: AdminDep, session: DbSessionDep) -> ConfigListResponse:
    """Return global runtime config plus overrides for the caller's agency."""
    agency_id = uuid.UUID(tenant.agency_id)
    stmt = (
        select(SystemConfig)
        .where(or_(SystemConfig.agency_id.is_(None), SystemConfig.agency_id == agency_id))
        .order_by(SystemConfig.key.asc(), SystemConfig.agency_id.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return ConfigListResponse(config=[_to_entry(row) for row in rows])


@router.patch("/config", response_model=ConfigPatchResponse)
async def patch_config(
    payload: ConfigPatchRequest,
    request: Request,
    tenant: AdminDep,
    session: DbSessionDep,
) -> ConfigPatchResponse:
    """Create/update one runtime config key; values are not echoed into audit metadata."""
    actor_id = require_actor(tenant)
    agency_id = _scope_id(tenant, agency_scoped=payload.agency_scoped)
    row = await _get_config_row(session, agency_id=agency_id, key=payload.key)
    if row is None:
        row = SystemConfig(
            agency_id=agency_id,
            key=payload.key,
            value=payload.value,
            updated_by=actor_id,
        )
        session.add(row)
    else:
        row.value = payload.value
        row.updated_by = actor_id
    await session.flush()
    await session.refresh(row)
    await audit_writer(tenant, session, request).record(
        actor_id=actor_id,
        action="config.update",
        resource_type="system_config",
        resource_id=str(row.id),
        metadata={"key": row.key, "scope": "agency" if agency_id is not None else "global"},
    )
    await session.commit()
    return ConfigPatchResponse(entry=_to_entry(row))

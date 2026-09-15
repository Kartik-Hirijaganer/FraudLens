"""Summary: Non-production developer utility routes for governed local/demo workflows.
`POST /dev/seed`, `POST /dev/reset`, and the trusted synthetic-provenance marker remain
admin-gated, tenant-scoped, audited, and hard-disabled when `environment == "prod"`.

Key classes:
- (none)

Key functions:
- dev_seed: POST /dev/seed — acknowledge a non-production seed request and audit it.
- dev_reset: POST /dev/reset — acknowledge a non-production reset request and audit it.
- mark_synthetic_provenance: authorize one locally generated transaction for model egress.

Notes:
- Reset is deliberately non-destructive in v1. Future plans can wire a bounded reset job behind
  this audited contract without changing the public route.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.api.deps import (
    DbSessionDep,
    SettingsDep,
    audit_writer,
    get_admin_tenant,
    require_actor,
)
from fraudlens_backend.db.models import TransactionSource
from fraudlens_backend.db.repositories.transactions import TransactionRepository
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.dev import DevUtilityResponse
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.settings import AppSettings

router = APIRouter(tags=["dev"])

AdminDep = Annotated[TenantContext, Depends(get_admin_tenant)]


def _ensure_non_prod(settings: AppSettings) -> None:
    """Fail closed for developer utilities in production."""
    if settings.environment == "prod":
        raise AppError("dev_utility_disabled")


async def _record_dev_action(
    *,
    action: str,
    request: Request,
    tenant: TenantContext,
    session: AsyncSession,
    settings: AppSettings,
) -> DevUtilityResponse:
    """Audit a non-production dev utility request and return a standard acknowledgement."""
    _ensure_non_prod(settings)
    actor_id = require_actor(tenant)
    await audit_writer(tenant, session, request).record(
        actor_id=actor_id,
        action=f"dev.{action}",
        resource_type="dev_utility",
        resource_id=None,
        metadata={"action": action, "environment": settings.environment},
    )
    await session.commit()
    return DevUtilityResponse(action=action, status="accepted", agency_id=tenant.agency_id)


@router.post("/dev/seed", response_model=DevUtilityResponse)
async def dev_seed(
    request: Request, tenant: AdminDep, session: DbSessionDep, settings: SettingsDep
) -> DevUtilityResponse:
    """Acknowledge and audit a non-production seed request."""
    return await _record_dev_action(
        action="seed", request=request, tenant=tenant, session=session, settings=settings
    )


@router.post("/dev/reset", response_model=DevUtilityResponse)
async def dev_reset(
    request: Request, tenant: AdminDep, session: DbSessionDep, settings: SettingsDep
) -> DevUtilityResponse:
    """Acknowledge and audit a non-production reset request."""
    return await _record_dev_action(
        action="reset", request=request, tenant=tenant, session=session, settings=settings
    )


@router.post(
    "/dev/transactions/{transactionId}/synthetic-provenance",
    response_model=DevUtilityResponse,
)
async def mark_synthetic_provenance(
    transaction_id: Annotated[uuid.UUID, Path(alias="transactionId")],
    request: Request,
    tenant: AdminDep,
    session: DbSessionDep,
    settings: SettingsDep,
) -> DevUtilityResponse:
    """Mark one generated local transaction synthetic for the governed vLLM E2E pass."""
    _ensure_non_prod(settings)
    actor_id = require_actor(tenant)
    transaction = await TransactionRepository(session, uuid.UUID(tenant.agency_id)).get(
        transaction_id
    )
    if transaction is None:
        raise AppError("transaction_not_found")
    transaction.source = TransactionSource.SYNTHETIC_GENERATOR
    await audit_writer(tenant, session, request).record(
        actor_id=actor_id,
        action="dev.synthetic_provenance",
        resource_type="transaction",
        resource_id=str(transaction.id),
        metadata={"source": TransactionSource.SYNTHETIC_GENERATOR.value},
    )
    await session.commit()
    return DevUtilityResponse(
        action="synthetic-provenance",
        status="accepted",
        agency_id=tenant.agency_id,
    )

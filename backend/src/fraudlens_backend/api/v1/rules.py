"""Summary: The AML-rules CRUD API (plan §5.3 endpoint 14, §16 Phase 4). Every route is
scoped to the verified JWT `agency_id` (via `get_tenant`, never a path/body tenant) and
operates ONLY on the agency's own rule rows through `RuleRepository` — so a cross-tenant (or
global/baseline) rule id resolves to 404 with no existence leak (plan §6.4), exactly like the
transactions surface. Create dedups by `(agency_id, code)` (409 `duplicate_rule_code`); the
partial PATCH applies only the fields sent and **bumps the rule's `version`** server-side
(so a run's recorded `rules_version` fingerprint changes); enable/disable is just a PATCH of
`enabled`. The seeded baseline rules are global platform rows that power the engine via
`RuleRepository.load_definitions`; they are intentionally not editable from this tenant
surface (customize by creating an agency rule with the same code).

Key classes:
- (none)

Key functions:
- list_rules: GET /rules — the agency's own rules (its overrides), ordered by code.
- create_rule: POST /rules — create an agency-scoped rule (201; 409 on duplicate code).
- get_rule: GET /rules/{ruleId} — detail (404 when missing/global/cross-tenant).
- update_rule: PATCH /rules/{ruleId} — partial update incl. enable/disable; bumps version.
- delete_rule: DELETE /rules/{ruleId} — remove the agency's rule (204; 404 otherwise).

Notes:
- create/update refresh the row after flush so the server-defaulted created_at/updated_at
  are populated in the response without relying on dialect RETURNING behavior.
- The rule's identity (`code`) and dispatch `ruleType` are immutable after creation; the
  update body omits them by design (delete + recreate to change them).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.api.deps import (
    DbSessionDep,
    audit_writer,
    get_tenant,
    optional_actor,
)
from fraudlens_backend.db.models import AmlRule
from fraudlens_backend.db.repositories import RuleRepository
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.models.rules import (
    RuleCreateRequest,
    RuleListResponse,
    RuleResponse,
    RuleUpdateRequest,
)

router = APIRouter(tags=["rules"])

TenantDep = Annotated[TenantContext, Depends(get_tenant)]


def _repo(tenant: TenantContext, session: AsyncSession) -> RuleRepository:
    """Build an agency-scoped rule repository for the verified tenant."""
    return RuleRepository(session, uuid.UUID(tenant.agency_id))


def _to_response(rule: AmlRule) -> RuleResponse:
    """Project a persisted AmlRule row onto the API response model."""
    return RuleResponse(
        rule_id=str(rule.id),
        agency_id=str(rule.agency_id),
        code=rule.code,
        name=rule.name,
        description=rule.description,
        rule_type=rule.rule_type,
        params=dict(rule.params or {}),
        severity=rule.severity,
        weight=rule.weight,
        enabled=rule.enabled,
        version=rule.version,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


@router.get("/rules", response_model=RuleListResponse)
async def list_rules(tenant: TenantDep, session: DbSessionDep) -> RuleListResponse:
    """Return the agency's own rules (its custom overrides), ordered by code."""
    repo = _repo(tenant, session)
    rules = await repo.list_for_agency()
    return RuleListResponse(rules=[_to_response(rule) for rule in rules])


@router.post("/rules", response_model=RuleResponse, status_code=201)
async def create_rule(
    payload: RuleCreateRequest, request: Request, tenant: TenantDep, session: DbSessionDep
) -> RuleResponse:
    """Create an agency-scoped rule (201); 409 when its code already exists for the agency."""
    repo = _repo(tenant, session)
    if await repo.get_by_code(payload.code) is not None:
        raise AppError("duplicate_rule_code")
    rule = AmlRule(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        rule_type=payload.rule_type,
        params=payload.params,
        severity=payload.severity,
        weight=payload.weight,
        enabled=payload.enabled,
        version=1,
    )
    await repo.add(rule)
    await session.refresh(rule)
    await audit_writer(tenant, session, request).record(
        actor_id=optional_actor(tenant),
        action="rule.create",
        resource_type="aml_rule",
        resource_id=str(rule.id),
        metadata={"code": rule.code},
    )
    await session.commit()
    return _to_response(rule)


@router.get("/rules/{ruleId}", response_model=RuleResponse)
async def get_rule(
    rule_id: Annotated[uuid.UUID, Path(alias="ruleId")],
    tenant: TenantDep,
    session: DbSessionDep,
) -> RuleResponse:
    """Return one rule by id; 404 when missing, global, or owned by another agency."""
    repo = _repo(tenant, session)
    rule = await repo.get(rule_id)
    if rule is None:
        raise AppError("rule_not_found")
    return _to_response(rule)


@router.patch("/rules/{ruleId}", response_model=RuleResponse)
async def update_rule(
    rule_id: Annotated[uuid.UUID, Path(alias="ruleId")],
    payload: RuleUpdateRequest,
    request: Request,
    tenant: TenantDep,
    session: DbSessionDep,
) -> RuleResponse:
    """Apply a partial update (incl. enable/disable) to the agency's rule; bumps version."""
    repo = _repo(tenant, session)
    rule = await repo.get(rule_id)
    if rule is None:
        raise AppError("rule_not_found")
    if payload.name is not None:
        rule.name = payload.name
    if payload.description is not None:
        rule.description = payload.description
    if payload.params is not None:
        rule.params = payload.params
    if payload.severity is not None:
        rule.severity = payload.severity
    if payload.weight is not None:
        rule.weight = payload.weight
    if payload.enabled is not None:
        rule.enabled = payload.enabled
    rule.version = rule.version + 1
    await session.flush()
    await session.refresh(rule)
    await audit_writer(tenant, session, request).record(
        actor_id=optional_actor(tenant),
        action="rule.update",
        resource_type="aml_rule",
        resource_id=str(rule.id),
        metadata={"code": rule.code, "version": str(rule.version)},
    )
    await session.commit()
    return _to_response(rule)


@router.delete("/rules/{ruleId}", status_code=204)
async def delete_rule(
    rule_id: Annotated[uuid.UUID, Path(alias="ruleId")],
    request: Request,
    tenant: TenantDep,
    session: DbSessionDep,
) -> None:
    """Delete the agency's rule (204); 404 when missing, global, or cross-tenant."""
    repo = _repo(tenant, session)
    rule = await repo.get(rule_id)
    if rule is None:
        raise AppError("rule_not_found")
    await audit_writer(tenant, session, request).record(
        actor_id=optional_actor(tenant),
        action="rule.delete",
        resource_type="aml_rule",
        resource_id=str(rule.id),
        metadata={"code": rule.code},
    )
    await repo.delete(rule)
    await session.commit()

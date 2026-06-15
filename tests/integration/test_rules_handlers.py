"""Direct-call tests for the AML-rules handler coroutines (plan §16 Phase 4). These call the
endpoint functions in-loop (like the repository tests) to exercise — and cover — every handler
branch: create + duplicate, list, detail + not-found, partial update + version bump, and delete
+ not-found. The mutating handlers also write an audit row (Phase 12), so they take a request for
the correlation id; a minimal `_request()` supplies one. The HTTP wiring (auth, envelope,
cross-tenant routing) is covered separately in test_rules_api.py."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from fraudlens_backend.api.v1.rules import (
    create_rule,
    delete_rule,
    get_rule,
    list_rules,
    update_rule,
)
from fraudlens_backend.db.models import Agency, AmlRuleType, Severity
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.models.rules import RuleCreateRequest, RuleUpdateRequest


def _request() -> Request:
    """A minimal Starlette request (no bound request-id) for direct handler calls."""
    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}
    )


async def _tenant(session: AsyncSession) -> TenantContext:
    """Insert an agency and return its tenant context (the handler scope)."""
    agency = Agency(id=uuid.uuid4(), name="Acme", slug=f"a-{uuid.uuid4().hex[:8]}")
    session.add(agency)
    await session.flush()
    return TenantContext(agency_id=str(agency.id))


def _create(**overrides: object) -> RuleCreateRequest:
    """Build a valid RuleCreateRequest with per-test overrides."""
    params: dict[str, object] = {
        "code": "custom_velocity",
        "name": "Custom velocity",
        "rule_type": AmlRuleType.VELOCITY,
        "params": {"windowHours": 12, "maxCount": 3},
        "severity": Severity.HIGH,
        "weight": "1.5",
        "enabled": True,
    }
    params.update(overrides)
    return RuleCreateRequest(**params)  # type: ignore[arg-type]


async def test_create_then_duplicate_raises(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    created = await create_rule(_create(), _request(), tenant, db_session)
    assert created.code == "custom_velocity"
    assert created.version == 1
    assert created.agency_id == tenant.agency_id
    with pytest.raises(AppError) as excinfo:
        await create_rule(_create(), _request(), tenant, db_session)
    assert excinfo.value.code == "duplicate_rule_code"


async def test_list_returns_created_rules(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    await create_rule(_create(code="r2"), _request(), tenant, db_session)
    await create_rule(_create(code="r1"), _request(), tenant, db_session)
    listing = await list_rules(tenant, db_session)
    assert [rule.code for rule in listing.rules] == ["r1", "r2"]


async def test_get_detail_and_missing_raises(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    created = await create_rule(_create(), _request(), tenant, db_session)
    fetched = await get_rule(uuid.UUID(created.rule_id), tenant, db_session)
    assert fetched.rule_id == created.rule_id
    with pytest.raises(AppError) as excinfo:
        await get_rule(uuid.uuid4(), tenant, db_session)
    assert excinfo.value.code == "rule_not_found"


async def test_update_applies_fields_and_bumps_version(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    created = await create_rule(_create(), _request(), tenant, db_session)
    update = RuleUpdateRequest(
        name="Renamed",
        description="updated",
        params={"windowHours": 6, "maxCount": 2},
        severity=Severity.CRITICAL,
        weight="3.0",
        enabled=False,
    )
    updated = await update_rule(uuid.UUID(created.rule_id), update, _request(), tenant, db_session)
    assert updated.version == 2  # bumped server-side
    assert updated.name == "Renamed"
    assert updated.enabled is False
    assert updated.severity is Severity.CRITICAL
    assert updated.params == {"windowHours": 6, "maxCount": 2}


async def test_update_partial_only_enabled_leaves_other_fields(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    created = await create_rule(_create(), _request(), tenant, db_session)
    # Only `enabled` is sent: every other field's "skip when None" branch is exercised.
    updated = await update_rule(
        uuid.UUID(created.rule_id), RuleUpdateRequest(enabled=False), _request(), tenant, db_session
    )
    assert updated.enabled is False
    assert updated.name == created.name  # untouched
    assert updated.weight == created.weight  # untouched
    assert updated.version == 2  # version still bumps on any update


async def test_update_missing_raises(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    with pytest.raises(AppError) as excinfo:
        await update_rule(
            uuid.uuid4(), RuleUpdateRequest(enabled=False), _request(), tenant, db_session
        )
    assert excinfo.value.code == "rule_not_found"


async def test_delete_removes_and_missing_raises(db_session: AsyncSession) -> None:
    tenant = await _tenant(db_session)
    created = await create_rule(_create(), _request(), tenant, db_session)
    await delete_rule(uuid.UUID(created.rule_id), _request(), tenant, db_session)
    with pytest.raises(AppError) as excinfo:
        await get_rule(uuid.UUID(created.rule_id), tenant, db_session)
    assert excinfo.value.code == "rule_not_found"
    with pytest.raises(AppError):
        await delete_rule(uuid.UUID(created.rule_id), _request(), tenant, db_session)

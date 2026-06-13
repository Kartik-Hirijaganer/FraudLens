"""RuleRepository tests (plan §16 Phase 4): agency-scoped CRUD over `aml_rules`, dedup by
code, and `load_definitions` — the engine rule set merged from code defaults < global DB
rows < the agency's own rows (so an empty table still yields the six baseline rules)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import Agency, AmlRule, AmlRuleType, Severity
from fraudlens_backend.db.repositories import RuleRepository

_BASELINE_CODES = {
    "structuring",
    "velocity",
    "high_risk_geography",
    "round_amount",
    "threshold_evasion",
    "rapid_movement",
}


async def _agency(session: AsyncSession) -> uuid.UUID:
    """Insert and flush an agency, returning its id (FK target for agency-scoped rules)."""
    agency = Agency(name="Acme", slug=f"acme-{uuid.uuid4().hex[:8]}")
    session.add(agency)
    await session.flush()
    return agency.id


def _rule(code: str, *, weight: str = "1.0", agency_id: uuid.UUID | None = None) -> AmlRule:
    """Build a velocity AmlRule row with a given code/weight (global when agency_id is None)."""
    return AmlRule(
        agency_id=agency_id,
        code=code,
        name=code,
        rule_type=AmlRuleType.VELOCITY,
        params={},
        severity=Severity.MEDIUM,
        weight=Decimal(weight),
    )


async def test_add_stamps_agency_and_is_scoped(db_session: AsyncSession) -> None:
    repo_a = RuleRepository(db_session, await _agency(db_session))
    repo_b = RuleRepository(db_session, await _agency(db_session))
    created = await repo_a.add(_rule("velocity"))
    assert created.agency_id == repo_a.agency_id
    assert await repo_a.get(created.id) is not None
    assert await repo_b.get(created.id) is None  # B cannot read A's rule


async def test_get_by_code_is_agency_scoped(db_session: AsyncSession) -> None:
    repo = RuleRepository(db_session, await _agency(db_session))
    await repo.add(_rule("velocity"))
    assert await repo.get_by_code("velocity") is not None
    assert await repo.get_by_code("missing") is None


async def test_list_for_agency_orders_by_code(db_session: AsyncSession) -> None:
    repo = RuleRepository(db_session, await _agency(db_session))
    await repo.add(_rule("velocity"))
    await repo.add(_rule("aaa"))
    assert [row.code for row in await repo.list_for_agency()] == ["aaa", "velocity"]


async def test_load_definitions_returns_code_defaults_when_db_empty(
    db_session: AsyncSession,
) -> None:
    repo = RuleRepository(db_session, await _agency(db_session))
    definitions = await repo.load_definitions()
    assert {definition.code for definition in definitions} == _BASELINE_CODES
    assert len(definitions) == 6


async def test_load_definitions_merge_precedence(db_session: AsyncSession) -> None:
    agency_id = await _agency(db_session)
    db_session.add(_rule("velocity", weight="9"))  # GLOBAL override (agency_id NULL)
    await db_session.flush()
    repo = RuleRepository(db_session, agency_id)
    await repo.add(_rule("velocity", weight="7"))  # AGENCY override
    merged = {definition.code: definition for definition in await repo.load_definitions()}
    assert merged["velocity"].weight == Decimal("7")  # agency beats global beats default
    assert merged["round_amount"].weight == Decimal("0.5")  # untouched code default

    # A different agency sees the GLOBAL override, never agency A's row.
    other = RuleRepository(db_session, await _agency(db_session))
    other_merged = {definition.code: definition for definition in await other.load_definitions()}
    assert other_merged["velocity"].weight == Decimal("9")


async def test_delete_removes_rule(db_session: AsyncSession) -> None:
    repo = RuleRepository(db_session, await _agency(db_session))
    rule = await repo.add(_rule("velocity"))
    await repo.delete(rule)
    assert await repo.get(rule.id) is None

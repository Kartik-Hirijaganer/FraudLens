"""Summary: The AML-rule repository (plan §16 Phase 4 — "DB load of aml_rules"). It is the
seam between the persisted `aml_rules` rows and the pure `fraudlens_core` rules engine. CRUD
is strictly **agency-scoped**: every read/write filters by the bound `agency_id`, `add`
stamps it, and a cross-tenant (or global) id resolves to None — so an agency can manage only
its own rule rows, exactly like `TransactionRepository` (no existence leak, plan §6.4). The
seeded baseline rules are GLOBAL (`agency_id IS NULL`) platform rows, so they are not mutable
through this tenant API; an agency customizes one by creating an agency-scoped row with the
same `code` (the engine merge gives it precedence). `load_definitions` produces the effective
rule set the engine evaluates: the code defaults, overlaid by global DB rows, overlaid by
this agency's rows — so rules still work if `aml_rules` is empty or unavailable (plan §11).

Key classes:
- RuleRepository: agency-scoped CRUD over `aml_rules` + the merged engine rule-set loader.

Key functions:
- (none)

Notes:
- `aml_rules.agency_id` is nullable (NULL = global), so this does NOT extend
  `TenantScopedRepository` (which requires NOT NULL `agency_id`); scoping is explicit here.
- `load_definitions` reads global + agency rows in one query, then `merge_definitions`
  (code defaults < global < agency) yields the effective definitions for `RuleRegistry`.
- `add` never writes a global row; platform/global rules are created by the seed, not by a
  tenant request — keeping cross-tenant rule changes impossible from the agency surface.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import AmlRule
from fraudlens_core import DEFAULT_RULE_DEFINITIONS, RuleDefinition, merge_definitions


def _to_definition(row: AmlRule) -> RuleDefinition:
    """Project a persisted AmlRule row onto the pure-core RuleDefinition (engine input)."""
    return RuleDefinition(
        code=row.code,
        name=row.name,
        rule_type=row.rule_type,
        params=dict(row.params or {}),
        severity=row.severity.value,
        weight=row.weight,
        enabled=row.enabled,
        version=row.version,
    )


class RuleRepository:
    """Agency-scoped CRUD over `aml_rules` plus the merged engine rule-set loader."""

    def __init__(self, session: AsyncSession, agency_id: uuid.UUID) -> None:
        """Bind the session and the agency scope every operation is filtered by."""
        self._session = session
        self._agency_id = agency_id

    @property
    def agency_id(self) -> uuid.UUID:
        """The tenant (agency) scope this repository enforces."""
        return self._agency_id

    async def get(self, rule_id: uuid.UUID) -> AmlRule | None:
        """Return the agency's rule with this id, or None (global/cross-tenant ids excluded)."""
        stmt = select(AmlRule).where(AmlRule.id == rule_id, AmlRule.agency_id == self._agency_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_code(self, code: str) -> AmlRule | None:
        """Return the agency's rule with this code, or None (dedup key for create)."""
        stmt = select(AmlRule).where(AmlRule.agency_id == self._agency_id, AmlRule.code == code)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_agency(self) -> Sequence[AmlRule]:
        """Return this agency's own rule rows ordered by code (its custom overrides)."""
        stmt = (
            select(AmlRule).where(AmlRule.agency_id == self._agency_id).order_by(AmlRule.code.asc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def add(self, rule: AmlRule) -> AmlRule:
        """Stamp the row's agency_id to the bound scope, persist (flush), and return it."""
        rule.agency_id = self._agency_id
        self._session.add(rule)
        await self._session.flush()
        return rule

    async def delete(self, rule: AmlRule) -> None:
        """Delete an (already agency-scoped) rule row and flush."""
        await self._session.delete(rule)
        await self._session.flush()

    async def load_definitions(self) -> tuple[RuleDefinition, ...]:
        """Return the effective rule set: code defaults < global DB rows < this agency's rows."""
        stmt = select(AmlRule).where(
            or_(AmlRule.agency_id.is_(None), AmlRule.agency_id == self._agency_id)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        global_defs = [_to_definition(row) for row in rows if row.agency_id is None]
        agency_defs = [_to_definition(row) for row in rows if row.agency_id is not None]
        return merge_definitions(DEFAULT_RULE_DEFINITIONS, global_defs, agency_defs)

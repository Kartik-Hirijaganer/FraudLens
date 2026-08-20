"""Summary: Fail-closed readers for tenant-aware runtime configuration used by
the multi-agent SAR workflow. Global `system_config` values are overlaid by the
verified agency's row, while malformed or unavailable configuration disables the
feature and denies live spend.

Key classes:
- RuntimeFeatureFlags: typed feature flags resolved for one agency.

Key functions:
- load_feature_flags: resolve global plus agency feature flags, failing closed.
- load_llm_daily_budget_usd: resolve the agency's daily LLM budget, failing closed.

Notes:
- These readers never accept an agency id from an agent or request body; callers pass
  the JWT-verified tenant scope.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import SystemConfig

_FEATURE_FLAGS_KEY = "featureFlags"
_LLM_DAILY_BUDGET_KEY = "llmDailyBudgetUsd"
_DENY_SPEND = Decimal("0")


class RuntimeFeatureFlags(BaseModel):
    """Typed tenant runtime flags; unknown flags remain forward-compatible."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    multi_agent_sar: bool = Field(
        default=False,
        alias="multiAgentSar",
        description="Whether this agency permits the bounded multi-agent SAR workflow.",
    )


async def _rows_for_key(
    session: AsyncSession, *, agency_id: uuid.UUID, key: str
) -> list[SystemConfig]:
    """Return global and verified-agency values for a runtime key."""
    stmt = select(SystemConfig).where(
        SystemConfig.key == key,
        or_(SystemConfig.agency_id.is_(None), SystemConfig.agency_id == agency_id),
    )
    return list((await session.execute(stmt)).scalars().all())


def _overlay(rows: list[SystemConfig]) -> list[Any]:
    """Return global values first and tenant overrides last, independent of DB row order."""
    return [row.value for row in rows if row.agency_id is None] + [
        row.value for row in rows if row.agency_id is not None
    ]


async def load_feature_flags(session: AsyncSession, *, agency_id: uuid.UUID) -> RuntimeFeatureFlags:
    """Resolve `featureFlags` for an agency; any read/shape error disables the feature."""
    try:
        values = _overlay(await _rows_for_key(session, agency_id=agency_id, key=_FEATURE_FLAGS_KEY))
        merged: dict[str, Any] = {}
        for value in values:
            if not isinstance(value, dict):
                return RuntimeFeatureFlags()
            merged.update(value)
        return RuntimeFeatureFlags.model_validate(merged)
    except Exception:
        return RuntimeFeatureFlags()


async def load_llm_daily_budget_usd(session: AsyncSession, *, agency_id: uuid.UUID) -> Decimal:
    """Resolve a positive daily USD cap; missing, malformed, or failed reads deny spend."""
    try:
        values = _overlay(
            await _rows_for_key(session, agency_id=agency_id, key=_LLM_DAILY_BUDGET_KEY)
        )
        if not values:
            return _DENY_SPEND
        raw = values[-1]
        if isinstance(raw, bool):
            return _DENY_SPEND
        parsed = Decimal(str(raw))
        return parsed if parsed > 0 else _DENY_SPEND
    except Exception:
        return _DENY_SPEND

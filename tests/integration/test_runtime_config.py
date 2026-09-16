"""Behavioral tests for the tenant feature-flag matrix and fail-closed runtime readers."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from portfolio_demo_identity import DEMO_AGENCY_ID
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import Agency, SystemConfig
from fraudlens_backend.db.repositories import load_feature_flags, load_llm_daily_budget_usd
from fraudlens_backend.pipeline_wiring import resolve_workflow_mode
from fraudlens_backend.settings import AppSettings


@pytest.mark.parametrize(
    ("settings_enabled", "agency_enabled", "expected"),
    [
        (False, False, "single_writer"),
        (True, False, "single_writer"),
        (False, True, "single_writer"),
        (True, True, "multi_agent"),
    ],
)
async def test_workflow_feature_requires_settings_and_agency_flags(
    db_session: AsyncSession,
    settings_enabled: bool,
    agency_enabled: bool,
    expected: str,
) -> None:
    """Only the two-key intersection enables the agent workflow."""
    db_session.add(Agency(id=DEMO_AGENCY_ID, name="Runtime", slug="runtime-flags"))
    await db_session.flush()
    db_session.add(
        SystemConfig(
            agency_id=DEMO_AGENCY_ID,
            key="featureFlags",
            value={"multiAgentSar": agency_enabled},
        )
    )
    await db_session.flush()

    resolved = await resolve_workflow_mode(
        db_session,
        settings=AppSettings(multi_agent_sar_enabled=settings_enabled),
        agency_id=DEMO_AGENCY_ID,
    )

    assert resolved == expected
    await db_session.execute(delete(SystemConfig))
    await db_session.execute(delete(Agency))
    await db_session.commit()


async def test_feature_flag_reader_fails_closed_on_database_error() -> None:
    """A runtime database failure cannot enable multi-agent execution."""

    class BrokenSession:
        async def execute(self, _statement: object) -> Any:
            raise OSError("database unavailable")

    flags = await load_feature_flags(  # type: ignore[arg-type]
        BrokenSession(), agency_id=DEMO_AGENCY_ID
    )

    assert flags.multi_agent_sar is False


async def test_a_database_failure_denies_llm_spend_outright() -> None:
    """An unreadable budget must refuse live spend, never fall back to the ceiling."""

    class BrokenSession:
        async def execute(self, _statement: object) -> Any:
            raise OSError("database unavailable")

    budget = await load_llm_daily_budget_usd(  # type: ignore[arg-type]
        BrokenSession(), agency_id=DEMO_AGENCY_ID, ceiling_usd=Decimal("0.25")
    )

    assert budget == Decimal("0")


@pytest.mark.parametrize(
    ("configured", "ceiling", "expected"),
    [
        # The deployment ceiling binds whenever the tenant asks for more than it admits.
        ("5", "0.25", "0.25"),
        # A tenant that asks for less than the ceiling keeps its own, stricter budget.
        ("0.10", "0.25", "0.10"),
        ("0.25", "0.25", "0.25"),
        # A non-positive tenant value denies spend outright rather than inheriting the ceiling.
        ("0", "0.25", "0"),
        ("-1", "0.25", "0"),
    ],
)
async def test_the_deployment_ceiling_bounds_every_tenant_budget(
    db_session: AsyncSession, configured: str, ceiling: str, expected: str
) -> None:
    db_session.add(Agency(id=DEMO_AGENCY_ID, name="Runtime", slug="runtime-budget"))
    await db_session.flush()
    db_session.add(
        SystemConfig(agency_id=DEMO_AGENCY_ID, key="llmDailyBudgetUsd", value=float(configured))
    )
    await db_session.flush()

    budget = await load_llm_daily_budget_usd(
        db_session, agency_id=DEMO_AGENCY_ID, ceiling_usd=Decimal(ceiling)
    )

    assert budget == Decimal(expected)
    await db_session.execute(delete(SystemConfig))
    await db_session.execute(delete(Agency))
    await db_session.commit()


async def test_an_unset_tenant_budget_denies_spend_even_under_a_generous_ceiling(
    db_session: AsyncSession,
) -> None:
    db_session.add(Agency(id=DEMO_AGENCY_ID, name="Runtime", slug="runtime-unset"))
    await db_session.flush()

    budget = await load_llm_daily_budget_usd(
        db_session, agency_id=DEMO_AGENCY_ID, ceiling_usd=Decimal("100")
    )

    assert budget == Decimal("0")
    await db_session.execute(delete(Agency))
    await db_session.commit()

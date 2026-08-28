"""Behavioral tests for the Phase 6 tenant feature-flag matrix and fail-closed readers."""

from __future__ import annotations

from typing import Any

import pytest
from portfolio_demo_identity import DEMO_AGENCY_ID
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import Agency, SystemConfig
from fraudlens_backend.db.repositories import load_feature_flags
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

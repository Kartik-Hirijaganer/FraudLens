"""Deferred upload drafting preserves source refusals and soft budget failures."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from pipeline_fakes import FakeSarDrafter

from fraudlens_backend.agents.runtime import AgentBudgetExceededError
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.pipeline_drafting import DeferredUploadDrafter
from fraudlens_backend.sar.budget import SarBudgetExceededError
from fraudlens_ml.sar import SarDraftStatus, SarEventType


async def test_live_upload_is_refused_before_drafter_construction(make_sar_input: Any) -> None:
    builder = AsyncMock()
    drafter = DeferredUploadDrafter(builder, live=True)
    events = [event async for event in drafter.draft(make_sar_input(source="api-upload"))]
    builder.assert_not_awaited()
    assert len(events) == 1 and events[0].type is SarEventType.FAILED
    assert events[0].result.error_code == "egress_source_not_allowed"


@pytest.mark.parametrize(
    "error",
    [
        AgentBudgetExceededError("cap"),
        SarBudgetExceededError("cap"),
        AppError("llm_budget_exceeded"),
    ],
)
async def test_budget_admission_failure_becomes_an_explicit_saved_outcome(
    make_sar_input: Any, error: Exception
) -> None:
    builder = AsyncMock(side_effect=error)
    events = [
        event async for event in DeferredUploadDrafter(builder, live=False).draft(make_sar_input())
    ]
    assert events[-1].result.status is SarDraftStatus.FAILED
    assert events[-1].result.error_code == "llm_budget_exceeded"


async def test_mock_upload_reuses_the_normal_drafter(make_sar_input: Any) -> None:
    builder = AsyncMock(return_value=FakeSarDrafter())
    events = [
        event
        async for event in DeferredUploadDrafter(builder, live=False).draft(
            make_sar_input(source="api-upload")
        )
    ]
    builder.assert_awaited_once()
    assert events[-1].result.status is SarDraftStatus.DRAFT

"""Unit tests for deterministic mock-agent lifecycle and live-only fallback enforcement."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from fraudlens_backend.agents.config import AgentRole, load_agents_config
from fraudlens_backend.agents.contracts import AgentExecutionRecord
from fraudlens_backend.agents.mock import MockAgentTeam
from fraudlens_backend.agents.prompts import AgentPromptTemplate
from fraudlens_backend.agents.tools import AGENT_TOOL_NAMES
from fraudlens_backend.sar.drafter_fallback import LiveAgentFallbackDrafter
from fraudlens_backend.sar.drafter_mock import MockSarDrafter
from fraudlens_backend.sar.prompt import SarPromptTemplate
from fraudlens_llm import get_llm_settings, load_catalog
from fraudlens_ml.sar import (
    SarDraftResult,
    SarDraftStatus,
    SarEventType,
    SarInput,
    SarStreamEvent,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _team(run_id: uuid.UUID, records: list[AgentExecutionRecord]) -> MockAgentTeam:
    """Build the committed deterministic mock team with a recording callback."""
    catalog = load_catalog(get_llm_settings().catalog_path)
    config = load_agents_config(
        catalog=catalog,
        available_tools=AGENT_TOOL_NAMES,
        path=_REPO_ROOT / "config" / "llm" / "agents.yml",
    )
    prompts = {
        role: AgentPromptTemplate.load(role, config.agents.for_role(role).prompt_id)
        for role in AgentRole
    }

    async def record(item: AgentExecutionRecord) -> None:
        records.append(item)

    return MockAgentTeam(
        run_id=run_id,
        config=config,
        prompts=prompts,
        single_writer=MockSarDrafter(SarPromptTemplate.load()),
        record_execution=record,
        request_revision=True,
    )


async def test_mock_team_emits_full_lifecycle_and_exactly_one_revision(make_sar_input) -> None:
    records: list[AgentExecutionRecord] = []
    events = [event async for event in _team(uuid.uuid4(), records).draft(make_sar_input())]

    assert sum(event.type is SarEventType.AGENT_STARTED for event in events) == 6
    assert sum(event.type is SarEventType.AGENT_COMPLETED for event in events) == 6
    assert sum(event.type is SarEventType.AGENT_REVISION_REQUESTED for event in events) == 1
    assert len(records) == 6
    assert all(record.model_call_count == 1 for record in records)
    assert events[-1].result is not None
    assert events[-1].result.workflow == "multi_agent"
    assert events[-1].result.revision_count == 1


class _TerminalDrafter:
    """Minimal drafter yielding one supplied terminal result."""

    def __init__(self, result: SarDraftResult) -> None:
        self.result = result
        self.called = False

    async def draft(self, _sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        self.called = True
        event_type = (
            SarEventType.COMPLETED
            if self.result.status is SarDraftStatus.DRAFT
            else SarEventType.FAILED
        )
        yield SarStreamEvent(type=event_type, result=self.result)


async def test_unrecoverable_agent_fault_uses_fallback(make_sar_input) -> None:
    primary = _TerminalDrafter(
        SarDraftResult(
            status=SarDraftStatus.FAILED,
            model_id="live-agent",
            prompt_version="v1",
            prompt_hash="h",
            error_code="agent_workflow_error",
            workflow="multi_agent",
        )
    )
    fallback = _TerminalDrafter(
        SarDraftResult(
            status=SarDraftStatus.DRAFT,
            content="safe live fallback",
            model_id="live-single-writer",
            prompt_version="v1",
            prompt_hash="h",
        )
    )

    events = [
        event
        async for event in LiveAgentFallbackDrafter(primary=primary, fallback=fallback).draft(
            make_sar_input()
        )
    ]

    assert fallback.called is True
    assert len(events) == 1
    assert events[0].result is not None
    assert events[0].result.model_id == "live-single-writer"


def test_live_fallback_rejects_mock_drafter() -> None:
    mock = MockSarDrafter(SarPromptTemplate.load())

    with pytest.raises(TypeError, match="never use the mock"):
        LiveAgentFallbackDrafter(primary=mock, fallback=mock)

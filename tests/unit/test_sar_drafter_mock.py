"""Unit tests for the keyless mock SAR drafter + drafter factory selection (plan §7.7, §16 P7)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fraudlens_backend.sar import MockSarDrafter, SarPromptTemplate, build_sar_drafter
from fraudlens_ml.sar import SarDraftContent, SarDraftStatus, SarEventType


async def _draft(drafter, sar_input):
    return [event async for event in drafter.draft(sar_input)]


@pytest.mark.asyncio
async def test_mock_streams_schema_valid_grounded_sar_without_keys(make_sar_input) -> None:
    drafter = MockSarDrafter(SarPromptTemplate.load())
    events = await _draft(drafter, make_sar_input())

    tokens = [e for e in events if e.type == SarEventType.TOKEN]
    terminal = events[-1]
    result = terminal.result
    assert terminal.type == SarEventType.COMPLETED
    assert result.status == SarDraftStatus.DRAFT
    assert result.model_id == "mock"  # no provider / no keys
    assert result.cost_usd == Decimal("0")
    # schema-valid structured body + grounded citations (only ids that were provided)
    assert isinstance(result.structured, SarDraftContent)
    assert result.structured.cited_regulations == ("31 CFR 1010.314",)
    # streamed tokens reconstruct the persisted content
    assert "".join(t.token or "" for t in tokens) == result.content
    assert result.prompt_version == "v1@1.0.0"


@pytest.mark.asyncio
async def test_mock_is_deterministic(make_sar_input) -> None:
    drafter = MockSarDrafter(SarPromptTemplate.load())
    first = await _draft(drafter, make_sar_input())
    second = await _draft(drafter, make_sar_input())
    assert first[-1].result.content == second[-1].result.content


@pytest.mark.asyncio
async def test_mock_handles_no_rules_features_or_citations(make_sar_input) -> None:
    drafter = MockSarDrafter(SarPromptTemplate.load())
    events = await _draft(drafter, make_sar_input(rule_hits=(), top_features=(), citations=()))
    structured = events[-1].result.structured
    assert structured.cited_regulations == ()
    assert "no deterministic rules" in structured.narrative
    assert "No specific regulatory citation matched." in structured.sections[-1].body


def test_factory_selects_mock_drafter_in_mock_mode(make_settings) -> None:
    drafter = build_sar_drafter(make_settings(llm_mode="mock"))
    assert isinstance(drafter, MockSarDrafter)

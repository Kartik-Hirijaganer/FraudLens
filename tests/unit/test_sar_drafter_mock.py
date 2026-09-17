"""Unit tests for the keyless mock SAR drafter + drafter factory selection (plan §7.7, §16 P7)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fraudlens_backend.sar import MockSarDrafter, SarPromptTemplate, build_sar_drafter
from fraudlens_ml.sar import SarDraftContent, SarDraftStatus, SarEventType, SarGateReason


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
    assert result.prompt_version == "v5@5.0.0"
    # The mock is not trusted either: it is ACCEPTED by the same deterministic gate as live.
    assert result.quality is not None
    assert result.quality.passed is True
    assert result.quality.reasons == ()
    assert result.structured.claims  # claim-level evidence refs exist
    assert all(claim.evidence_refs for claim in result.structured.claims)
    assert {section.heading for section in result.structured.sections} == {
        "Who",
        "What",
        "When",
        "Where",
        "Why",
        "How",
    }


@pytest.mark.asyncio
async def test_mock_is_deterministic(make_sar_input) -> None:
    drafter = MockSarDrafter(SarPromptTemplate.load())
    first = await _draft(drafter, make_sar_input())
    second = await _draft(drafter, make_sar_input())
    assert first[-1].result.content == second[-1].result.content


@pytest.mark.asyncio
async def test_mock_without_citations_is_rejected_rather_than_silently_uncited(
    make_sar_input,
) -> None:
    """`require_citation` binds the mock too: an uncitable case fails instead of drafting."""
    drafter = MockSarDrafter(SarPromptTemplate.load())
    events = await _draft(drafter, make_sar_input(rule_hits=(), top_features=(), citations=()))
    result = events[-1].result

    assert events[-1].type == SarEventType.FAILED
    assert result.status == SarDraftStatus.FAILED
    assert result.error_code == "sar_quality_gate_failed"
    assert result.quality is not None
    assert SarGateReason.NO_CITATIONS in result.quality.reasons


@pytest.mark.asyncio
async def test_mock_refuses_an_egress_ineligible_source(make_sar_input) -> None:
    """A source the egress policy does not allow fails the same way it does on the live path."""
    drafter = MockSarDrafter(SarPromptTemplate.load())
    events = await _draft(drafter, make_sar_input(source="api-upload"))

    assert events[-1].type == SarEventType.FAILED
    assert events[-1].result.error_code == "egress_source_not_allowed"


def test_factory_selects_mock_drafter_in_mock_mode(make_settings) -> None:
    drafter = build_sar_drafter(make_settings(llm_mode="mock"))
    assert isinstance(drafter, MockSarDrafter)

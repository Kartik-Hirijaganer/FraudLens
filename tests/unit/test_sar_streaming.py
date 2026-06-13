"""Unit tests for the shared SAR token-streaming helper (plan §10.2)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fraudlens_backend.sar.streaming import stream_result
from fraudlens_ml.sar import SarDraftResult, SarDraftStatus, SarEventType


def _result(*, status: SarDraftStatus, content: str = "") -> SarDraftResult:
    return SarDraftResult(
        status=status,
        content=content,
        model_id="mock",
        prompt_version="v1@1.0.0",
        prompt_hash="h",
        cost_usd=Decimal("0"),
    )


@pytest.mark.asyncio
async def test_stream_result_emits_tokens_then_completed() -> None:
    result = _result(status=SarDraftStatus.DRAFT, content="alpha beta gamma")
    events = [event async for event in stream_result(result)]
    tokens = [e for e in events if e.type == SarEventType.TOKEN]
    assert events[-1].type == SarEventType.COMPLETED
    assert events[-1].result is result
    assert "".join(t.token or "" for t in tokens) == "alpha beta gamma"


@pytest.mark.asyncio
async def test_stream_result_failed_emits_single_failed_event() -> None:
    result = _result(status=SarDraftStatus.FAILED)
    events = [event async for event in stream_result(result)]
    assert len(events) == 1
    assert events[0].type == SarEventType.FAILED
    assert events[0].result is result


@pytest.mark.asyncio
async def test_stream_result_empty_content_only_completes() -> None:
    events = [event async for event in stream_result(_result(status=SarDraftStatus.DRAFT))]
    assert [e.type for e in events] == [SarEventType.COMPLETED]

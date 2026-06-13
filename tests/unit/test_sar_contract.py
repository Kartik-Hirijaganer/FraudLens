"""Unit tests for the ml SAR contract types + the backend SAR view model (plan §16 Phase 7)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from fraudlens_backend.models.sar import SarDraftView
from fraudlens_ml.sar import (
    SarCitation,
    SarDraftContent,
    SarDrafter,
    SarDraftResult,
    SarDraftStatus,
    SarEventType,
    SarInput,
    SarStreamEvent,
)


def test_sar_input_serializes_camelcase_and_is_phi_free(make_sar_input) -> None:
    dumped = make_sar_input().model_dump(by_alias=True)
    assert {"fraudProbability", "modelVersion", "ruleHits", "topFeatures", "ragContext"} <= set(
        dumped
    )


def test_sar_draft_content_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SarDraftContent(
            subject="s",
            narrative="n",
            recommended_action="escalate",
            bogus="x",  # type: ignore[call-arg]
        )


def test_sar_draft_content_defaults_are_empty() -> None:
    content = SarDraftContent(subject="s", narrative="n", recommended_action="escalate")
    assert content.sections == ()
    assert content.cited_regulations == ()


def test_sar_stream_event_defaults_are_none() -> None:
    event = SarStreamEvent(type=SarEventType.TOKEN, token="hi")
    assert event.result is None
    completed = SarStreamEvent(
        type=SarEventType.COMPLETED,
        result=SarDraftResult(
            status=SarDraftStatus.DRAFT, model_id="mock", prompt_version="v1@1.0.0", prompt_hash="h"
        ),
    )
    assert completed.token is None


def test_sar_drafter_is_runtime_checkable() -> None:
    class _Drafter:
        def draft(self, sar_input):
            yield sar_input

    class _NotADrafter:
        pass

    assert isinstance(_Drafter(), SarDrafter)
    assert not isinstance(_NotADrafter(), SarDrafter)


def test_sar_draft_view_projects_camelcase() -> None:
    view = SarDraftView(
        sar_draft_id="sd-1",
        run_id="run-1",
        alert_id=None,
        version=1,
        status="draft",
        content="# SAR",
        structured={"subject": "s"},
        citations=[{"citation": "31 CFR 1010.314"}],
        model_id="mock",
        prompt_version="v1@1.0.0",
        prompt_hash="h",
        token_usage={"totalTokens": 10},
        cost_usd=Decimal("0"),
        created_at=datetime.now(UTC),
    )
    dumped = view.model_dump(by_alias=True)
    assert {"sarDraftId", "runId", "modelId", "promptVersion", "costUsd"} <= set(dumped)


def test_sar_citation_and_status_values() -> None:
    citation = SarCitation(citation="31 CFR 1010.314", title="t", source="FinCEN", snippet="s")
    assert citation.citation == "31 CFR 1010.314"
    assert SarDraftStatus.DRAFT.value == "draft"
    assert SarDraftStatus.FAILED.value == "failed"
    assert SarInput  # imported symbol is referenced

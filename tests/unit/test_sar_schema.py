"""Unit tests for SAR structured-output parsing, citation grounding, and rendering (plan §8.1)."""

from __future__ import annotations

import pytest

from fraudlens_backend.sar.schema import (
    SarSchemaError,
    ground_citations,
    parse_and_ground,
    render_markdown,
)
from fraudlens_ml.sar import SarCitation, SarDraftContent

_VALID = (
    '{"subject":"Suspected structuring","narrative":"Narrative.",'
    '"sections":[{"heading":"Summary","body":"b"}],'
    '"citedRegulations":["31 CFR 1010.314","99 FAKE 1"],"recommendedAction":"Escalate"}'
)


def _citations() -> tuple[SarCitation, ...]:
    return (
        SarCitation(citation="31 CFR 1010.314", title="Structuring", source="FinCEN", snippet="s"),
    )


def test_parse_and_ground_drops_fabricated_citations() -> None:
    content, grounded = parse_and_ground(_VALID, _citations())
    assert content.cited_regulations == ("31 CFR 1010.314",)  # "99 FAKE 1" dropped (not provided)
    assert [c.citation for c in grounded] == ["31 CFR 1010.314"]
    assert content.subject == "Suspected structuring"


def test_parse_and_ground_tolerates_code_fence() -> None:
    fenced = f"```json\n{_VALID}\n```"
    content, _ = parse_and_ground(fenced, _citations())
    assert content.narrative == "Narrative."


def test_parse_and_ground_rejects_invalid_json() -> None:
    with pytest.raises(SarSchemaError):
        parse_and_ground("not json at all", _citations())


def test_parse_and_ground_rejects_schema_violation() -> None:
    with pytest.raises(SarSchemaError):
        parse_and_ground('{"subject":"s"}', _citations())  # missing required keys


def test_ground_citations_preserves_order_and_dedupes() -> None:
    available = (
        SarCitation(citation="A", title="a", source="FinCEN", snippet="s"),
        SarCitation(citation="B", title="b", source="FinCEN", snippet="s"),
    )
    grounded_ids, grounded = ground_citations(["B", "A", "B", "Z"], available)
    assert grounded_ids == ("B", "A")  # order preserved, deduped, "Z" (not provided) dropped
    assert [c.citation for c in grounded] == ["B", "A"]


def test_render_markdown_masks_phi_and_lists_citations() -> None:
    content = SarDraftContent(
        subject="Subj",
        narrative="Reach analyst@example.com about this.",
        sections=(),
        cited_regulations=("31 CFR 1010.314",),
        recommended_action="Escalate",
    )
    rendered = render_markdown(content)
    assert rendered.startswith("# Suspicious Activity Report")
    assert "analyst@example.com" not in rendered
    assert "[REDACTED_EMAIL]" in rendered
    assert "31 CFR 1010.314" in rendered


def test_render_markdown_handles_no_citations() -> None:
    content = SarDraftContent(subject="s", narrative="n", recommended_action="escalate")
    assert "**Cited regulations:** none" in render_markdown(content)

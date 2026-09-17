"""Unit tests for SAR structured-output parsing, citation grounding, and rendering (plan §8.1)."""

from __future__ import annotations

import pytest

from fraudlens_backend.sar.egress import load_egress_policy, project_for_model
from fraudlens_backend.sar.evidence import build_evidence_catalog
from fraudlens_backend.sar.schema import (
    SarSchemaError,
    ground_citations,
    parse_and_ground,
    parse_content,
    render_markdown,
    sar_response_schema,
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


def test_parse_content_preserves_ungrounded_claims_for_review() -> None:
    content = parse_content(_VALID)

    assert content.cited_regulations == ("31 CFR 1010.314", "99 FAKE 1")


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


def test_render_markdown_is_byte_identical_with_empty_claims() -> None:
    content = SarDraftContent(
        subject="Suspected structuring",
        narrative="The transaction pattern warrants review.",
        sections=(),
        cited_regulations=("31 CFR 1010.314",),
        recommended_action="Escalate for human review.",
    )

    assert render_markdown(content) == (
        "# Suspicious Activity Report (draft — pending human review)\n\n"
        "**Subject:** Suspected structuring\n\n"
        "The transaction pattern warrants review.\n\n"
        "**Cited regulations:** 31 CFR 1010.314\n\n"
        "**Recommended action:** Escalate for human review."
    )


def test_the_response_schema_forbids_any_citation_when_none_are_offered(
    make_sar_input,
) -> None:
    """With nothing to cite, constrained decoding makes citing structurally impossible."""
    model_input = project_for_model(
        make_sar_input(citations=(), rag_context=""), load_egress_policy()
    )
    schema = sar_response_schema((), build_evidence_catalog(model_input))

    cited = schema["properties"]["citedRegulations"]
    claim_ids = schema["$defs"]["SarClaim"]["properties"]["citationIds"]
    assert cited["maxItems"] == 0 and "enum" not in cited["items"]
    assert claim_ids["maxItems"] == 0 and "enum" not in claim_ids["items"]


def test_the_response_schema_closes_evidence_values_and_required_shape(make_sar_input) -> None:
    sar_input = make_sar_input()
    model_input = project_for_model(sar_input, load_egress_policy())
    catalog = build_evidence_catalog(model_input)

    schema = sar_response_schema(sar_input.citations, catalog)

    claim = schema["$defs"]["SarClaim"]
    refs = claim["properties"]["evidenceRefs"]["items"]["enum"]
    assert refs == [fact.ref for fact in catalog.facts]
    exact_pairs = schema["$defs"]["SarClaimFact"]["oneOf"]
    assert len(exact_pairs) == len(catalog.facts)
    amount_pair = next(
        item for item in exact_pairs if item["properties"]["ref"]["const"] == "txn.amount"
    )
    assert amount_pair["properties"]["value"]["const"] == "9500"
    core = schema["properties"]["claims"]["prefixItems"][0]
    assert core["properties"]["evidenceRefs"]["minItems"] == 8
    headings = [
        item["properties"]["heading"]["const"]
        for item in schema["properties"]["sections"]["prefixItems"]
    ]
    assert headings == ["Who", "What", "When", "Where", "Why", "How"]

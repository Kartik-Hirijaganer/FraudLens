"""Summary: The SAR generation envelope, response-schema guard, grounding, and safe rendering.
The live model returns a compact `SarGenerationContent`: prose, one claim statement, six section
bodies, and citation ids. The backend then hydrates the claim's evidence refs and asserted values
from the trusted `SarEvidenceCatalog`, adds the fixed section headings and human-review action, and
passes the resulting `SarDraftContent` through the unchanged production gate. This keeps canonical
facts outside model control while avoiding the large case-specific `oneOf` grammar that made live
constrained decoding take minutes. Legacy full-draft parsing stays available to replay old evidence.

Key classes:
- SarSchemaError: raised when model output is not valid against the SAR schema.
- SarSectionBodies: compact generated bodies for the fixed FinCEN section vocabulary.
- SarGenerationContent: the compact, provider-facing JSON boundary.

Key functions:
- ground_citations: keep only the claimed citation ids that were actually provided (drop the rest).
- parse_content: parse + validate model JSON into an ungrounded SarDraftContent.
- parse_only: the gate's entry point — parse without grounding, so fabrication stays visible.
- parse_generation: parse the compact provider response without grounding citations.
- hydrate_generation: construct trusted claim facts and the final SAR body deterministically.
- parse_and_ground: compose parsing and grounding into a SarDraftContent + its citations.
- sar_response_schema: close generated citation ids over the offered regulations.
- render_markdown: render a validated SAR body into PHI-masked, human-readable markdown.

Notes:
- Grounding is NO LONGER the guardrail against fabricated ids: since release 0.5.0 the production
  `SarQualityGate` runs on the ungrounded content and REJECTS a fabricated id instead of deleting
  it. `ground_citations` remains because the multi-agent workflow grounds after review, and
  because a gate-passed draft's grounding is then a no-op.
- `sar_response_schema` makes citation fabrication structurally impossible for constrained stages;
  evidence refs and asserted values are not generated at all and therefore cannot be altered.
- Grounding preserves the model's citation order and de-duplicates; the returned citation objects
  come from the trusted `available` list, never from the model, so titles/snippets are never forged.
- `parse_and_ground` strips a single leading/trailing fenced code block (``` or ```json) before
  parsing, since models often wrap JSON despite the instruction not to.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel

from fraudlens_backend.sar.evidence import SarEvidenceCatalog, required_narrative_facts
from fraudlens_core.phi import mask_text
from fraudlens_ml.sar import (
    SarCitation,
    SarClaim,
    SarClaimFact,
    SarDraftContent,
    SarSection,
)

_CODE_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n(?P<body>.*?)\n```\s*$", re.DOTALL | re.IGNORECASE
)
_FINCEN_SECTIONS = (
    ("Who", "who"),
    ("What", "what"),
    ("When", "when"),
    ("Where", "where"),
    ("Why", "why"),
    ("How", "how"),
)
_RECOMMENDED_ACTION = "Recommend human compliance review."


class SarSchemaError(ValueError):
    """Raised when model output cannot be parsed/validated as a SAR draft body."""


class SarSectionBodies(BaseModel):
    """Compact generated prose for the fixed FinCEN section headings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    who: str = Field(..., min_length=1, description="Who was involved, using masked aliases.")
    what: str = Field(..., min_length=1, description="What suspicious activity occurred.")
    when: str = Field(..., min_length=1, description="When the activity occurred.")
    where: str = Field(..., min_length=1, description="Where or through which channel it occurred.")
    why: str = Field(..., min_length=1, description="Why the activity warrants review.")
    how: str = Field(..., min_length=1, description="How the activity was conducted.")


class SarGenerationContent(BaseModel):
    """Small provider-facing response that the backend expands into a trusted SAR draft."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )

    subject: str = Field(..., min_length=1, description="Qualitative one-line subject.")
    narrative: str = Field(..., min_length=1, description="Concise SAR narrative.")
    claim_statement: str = Field(..., min_length=1, description="One core factual claim.")
    section_bodies: SarSectionBodies = Field(..., description="Six FinCEN section bodies.")
    citation_ids: tuple[str, ...] = Field(
        ..., description="Regulation ids supporting the generated narrative."
    )


def ground_citations(
    claimed: Sequence[str], available: Sequence[SarCitation]
) -> tuple[tuple[str, ...], tuple[SarCitation, ...]]:
    """Keep only claimed ids that were provided; this DROPS, it does not detect (see the gate)."""
    by_id = {citation.citation: citation for citation in available}
    grounded_ids: list[str] = []
    grounded: list[SarCitation] = []
    for citation_id in claimed:
        match = by_id.get(citation_id)
        if match is not None and citation_id not in grounded_ids:
            grounded_ids.append(citation_id)
            grounded.append(match)
    return tuple(grounded_ids), tuple(grounded)


def parse_content(raw_text: str) -> SarDraftContent:
    """Parse + validate model JSON into an ungrounded SarDraftContent."""
    payload = _strip_code_fence(raw_text)
    try:
        return SarDraftContent.model_validate_json(payload)
    except ValidationError as exc:
        raise SarSchemaError("model output is not a valid SAR draft") from exc


def parse_only(raw_text: str) -> SarDraftContent:
    """Parse + validate model JSON WITHOUT grounding, so fabricated ids stay visible to the gate."""
    return parse_content(raw_text)


def parse_generation(raw_text: str) -> SarGenerationContent:
    """Parse + validate the compact provider JSON without grounding its citation ids."""
    payload = _strip_code_fence(raw_text)
    try:
        return SarGenerationContent.model_validate_json(payload)
    except ValidationError as exc:
        raise SarSchemaError("model output is not a valid SAR generation envelope") from exc


def hydrate_generation(
    generated: SarGenerationContent, catalog: SarEvidenceCatalog
) -> SarDraftContent:
    """Expand generated prose with canonical claim facts and fixed production-owned fields."""
    facts = required_narrative_facts(catalog)
    claim = SarClaim(
        statement=generated.claim_statement,
        evidence_refs=tuple(fact.ref for fact in facts),
        citation_ids=generated.citation_ids,
        asserted_facts=tuple(SarClaimFact(ref=fact.ref, value=fact.value) for fact in facts),
    )
    sections = tuple(
        SarSection(heading=heading, body=getattr(generated.section_bodies, field))
        for heading, field in _FINCEN_SECTIONS
    )
    return SarDraftContent(
        subject=generated.subject,
        narrative=generated.narrative,
        claims=(claim,),
        sections=sections,
        cited_regulations=generated.citation_ids,
        recommended_action=_RECOMMENDED_ACTION,
    )


def sar_response_schema(
    available: Sequence[SarCitation], catalog: SarEvidenceCatalog
) -> dict[str, Any]:
    """Build the compact schema with citations closed over the offered regulation ids."""
    required_narrative_facts(catalog)
    schema = SarGenerationContent.model_json_schema(by_alias=True)
    offered = list(dict.fromkeys(citation.citation for citation in available))
    citation_ids = schema["properties"]["citationIds"]
    citation_ids["items"] = {"type": "string", "enum": offered} if offered else {"type": "string"}
    citation_ids["maxItems"] = len(offered)
    if offered:
        citation_ids["minItems"] = 1
    return schema


def parse_and_ground(
    raw_text: str, available: Sequence[SarCitation]
) -> tuple[SarDraftContent, tuple[SarCitation, ...]]:
    """Parse + validate model JSON into a SarDraftContent grounded against the citations."""
    content = parse_content(raw_text)
    grounded_ids, grounded = ground_citations(content.cited_regulations, available)
    return content.model_copy(update={"cited_regulations": grounded_ids}), grounded


def render_markdown(content: SarDraftContent) -> str:
    """Render a validated SAR body into PHI-masked, human-readable markdown text."""
    parts = [
        "# Suspicious Activity Report (draft — pending human review)",
        f"**Subject:** {content.subject}",
        content.narrative,
    ]
    parts.extend(f"## {section.heading}\n\n{section.body}" for section in content.sections)
    cited = ", ".join(content.cited_regulations) if content.cited_regulations else "none"
    parts.append(f"**Cited regulations:** {cited}")
    parts.append(f"**Recommended action:** {content.recommended_action}")
    return mask_text("\n\n".join(parts)).value


def _strip_code_fence(text: str) -> str:
    """Return the body of a single fenced code block, or the trimmed text when unfenced."""
    match = _CODE_FENCE_RE.match(text)
    return match.group("body") if match else text.strip()

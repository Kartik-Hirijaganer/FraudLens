"""Summary: The SAR structured-output schema guard + citation grounding + safe rendering (plan
§8.1, §16 Phase 7). The live drafter instructs the model to return a JSON `SarDraftContent`;
`parse_only` parses that JSON (tolerating a stray markdown code fence) and validates it against
the strict `SarDraftContent` schema (`extra="forbid"`) WITHOUT grounding, because the production
`SarQualityGate` decides on the ungrounded output. Grounding is no longer the "no fabricated ids"
guardrail it was described as before release 0.5.0: `ground_citations` silently DELETED a
fabricated id and returned a draft that looked clean, which is exactly the failure the gate now
rejects. It remains here because the multi-agent workflow grounds after review and because a
gate-passed draft's grounding is a no-op. `render_markdown` turns the validated body into the
human-readable SAR text that is persisted to `sar_drafts.content`, run through the deterministic
core masker so the stored/displayed text is PHI-safe even if the model echoed a PHI-shaped span.
All pure functions — no IO, no provider calls.

Key classes:
- SarSchemaError: raised when model output is not valid against the SAR schema.

Key functions:
- ground_citations: keep only the claimed citation ids that were actually provided (drop the rest).
- parse_content: parse + validate model JSON into an ungrounded SarDraftContent.
- parse_only: the gate's entry point — parse without grounding, so fabrication stays visible.
- parse_and_ground: compose parsing and grounding into a SarDraftContent + its citations.
- sar_response_schema: close citations, evidence refs, asserted values, and required sections.
- render_markdown: render a validated SAR body into PHI-masked, human-readable markdown.

Notes:
- Grounding is NO LONGER the guardrail against fabricated ids: since release 0.5.0 the production
  `SarQualityGate` runs on the ungrounded content and REJECTS a fabricated id instead of deleting
  it. `ground_citations` remains because the multi-agent workflow grounds after review, and
  because a gate-passed draft's grounding is then a no-op.
- `sar_response_schema` makes fabrication structurally impossible rather than merely detectable:
  the offered ids become a closed JSON-Schema enum on `citedRegulations` and `claims[].citationIds`,
  so a constrained-decoding stage cannot emit an id it was never given.
- Grounding preserves the model's citation order and de-duplicates; the returned citation objects
  come from the trusted `available` list, never from the model, so titles/snippets are never forged.
- `parse_and_ground` strips a single leading/trailing fenced code block (``` or ```json) before
  parsing, since models often wrap JSON despite the instruction not to.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from fraudlens_backend.sar.evidence import SarEvidenceCatalog, required_narrative_facts
from fraudlens_core.phi import mask_text
from fraudlens_ml.sar import SarCitation, SarDraftContent, SarEvidenceFact

_CODE_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n(?P<body>.*?)\n```\s*$", re.DOTALL | re.IGNORECASE
)
_CLAIM_DEF = "SarClaim"
_CLAIM_FACT_DEF = "SarClaimFact"
_CITATION_ID_FIELDS = ("citedRegulations", "citationIds")
_FINCEN_SECTIONS = ("Who", "What", "When", "Where", "Why", "How")


class SarSchemaError(ValueError):
    """Raised when model output cannot be parsed/validated as a SAR draft body."""


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


def sar_response_schema(
    available: Sequence[SarCitation], catalog: SarEvidenceCatalog
) -> dict[str, Any]:
    """Build the strict schema closed over every offered citation and trusted evidence fact."""
    schema = SarDraftContent.model_json_schema(by_alias=True)
    offered = list(dict.fromkeys(citation.citation for citation in available))
    defs = schema.get("$defs", {})
    claim = defs.get(_CLAIM_DEF, {}).get("properties", {}) if isinstance(defs, dict) else {}
    for container in (schema.get("properties", {}), claim):
        _close_citation_ids(container, offered)
    _close_claim_evidence(schema, catalog)
    _require_core_claim(schema, catalog, offered)
    _require_fincen_sections(schema)
    _require_root_arrays(schema, offered)
    return schema


def _close_citation_ids(properties: dict[str, Any], offered: list[str]) -> None:
    """Constrain a citation-id array to exactly the offered ids, or to empty when none exist."""
    for field_name in _CITATION_ID_FIELDS:
        field = properties.get(field_name)
        if not isinstance(field, dict):
            continue
        if offered:
            field["items"] = {"type": "string", "enum": offered}
        else:
            field["items"] = {"type": "string"}
            field["maxItems"] = 0


def _closed_fact_schema(fact: SarEvidenceFact) -> dict[str, Any]:
    """Return one exact ref/value object accepted by constrained decoding."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "ref": {"type": "string", "const": fact.ref},
            "value": {"type": "string", "const": fact.value},
        },
        "required": ["ref", "value"],
    }


def _close_claim_evidence(schema: dict[str, Any], catalog: SarEvidenceCatalog) -> None:
    """Forbid invented evidence refs and ref/value pairs in every generated claim."""
    defs = schema["$defs"]
    claim = defs[_CLAIM_DEF]
    properties = claim["properties"]
    refs = [fact.ref for fact in catalog.facts]
    properties["evidenceRefs"].update({"items": {"type": "string", "enum": refs}, "minItems": 1})
    defs[_CLAIM_FACT_DEF] = {
        "title": _CLAIM_FACT_DEF,
        "oneOf": [_closed_fact_schema(fact) for fact in catalog.facts],
    }
    claim["required"] = ["statement", "evidenceRefs", "citationIds", "assertedFacts"]


def _exact_array(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Return an ordered, fixed-length array schema."""
    return {
        "type": "array",
        "prefixItems": items,
        "items": False,
        "minItems": len(items),
        "maxItems": len(items),
    }


def _citation_array(offered: list[str]) -> dict[str, Any]:
    """Return a deduplicated array closed over offered regulation ids."""
    if not offered:
        return {"type": "array", "items": {"type": "string"}, "maxItems": 0}
    return {
        "type": "array",
        "items": {"type": "string", "enum": offered},
        "maxItems": len(offered),
    }


def _require_core_claim(
    schema: dict[str, Any], catalog: SarEvidenceCatalog, offered: list[str]
) -> None:
    """Make the first claim carry every core narrative fact in deterministic order."""
    facts = required_narrative_facts(catalog)
    core_claim = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "statement": {"type": "string", "minLength": 1},
            "evidenceRefs": _exact_array([{"type": "string", "const": fact.ref} for fact in facts]),
            "citationIds": _citation_array(offered),
            "assertedFacts": _exact_array([_closed_fact_schema(fact) for fact in facts]),
        },
        "required": ["statement", "evidenceRefs", "citationIds", "assertedFacts"],
    }
    claims = schema["properties"]["claims"]
    claims.update(
        {
            "prefixItems": [core_claim],
            "items": {"$ref": f"#/$defs/{_CLAIM_DEF}"},
            "minItems": 1,
            "maxItems": 3,
        }
    )


def _require_fincen_sections(schema: dict[str, Any]) -> None:
    """Make all six ordered FinCEN sections structural instead of prompt-only."""
    section_schemas = [
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "heading": {"type": "string", "const": heading},
                "body": {"type": "string", "minLength": 1},
            },
            "required": ["heading", "body"],
        }
        for heading in _FINCEN_SECTIONS
    ]
    schema["properties"]["sections"] = _exact_array(section_schemas)


def _require_root_arrays(schema: dict[str, Any], offered: list[str]) -> None:
    """Require the arrays production relies on and at least one offered citation."""
    required = schema["required"]
    required.extend(
        name for name in ("claims", "sections", "citedRegulations") if name not in required
    )
    citations = schema["properties"]["citedRegulations"]
    if offered:
        citations["minItems"] = 1
        citations["maxItems"] = len(offered)


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

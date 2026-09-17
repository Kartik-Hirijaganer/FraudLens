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
- sar_response_schema: build the strict SAR schema with a CLOSED citation-id enum.
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

from fraudlens_core.phi import mask_text
from fraudlens_ml.sar import SarCitation, SarDraftContent

_CODE_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n(?P<body>.*?)\n```\s*$", re.DOTALL | re.IGNORECASE
)
_CLAIM_DEF = "SarClaim"
_CITATION_ID_FIELDS = ("citedRegulations", "citationIds")


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


def sar_response_schema(available: Sequence[SarCitation]) -> dict[str, Any]:
    """Build the strict SAR draft schema whose citation-id lists are closed over offered ids."""
    schema = SarDraftContent.model_json_schema(by_alias=True)
    offered = list(dict.fromkeys(citation.citation for citation in available))
    defs = schema.get("$defs", {})
    claim = defs.get(_CLAIM_DEF, {}).get("properties", {}) if isinstance(defs, dict) else {}
    for container in (schema.get("properties", {}), claim):
        _close_citation_ids(container, offered)
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

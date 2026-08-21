"""Summary: The SAR structured-output schema guard + citation grounding + safe rendering (plan
§8.1, §16 Phase 7). The live drafter instructs the model to return a JSON `SarDraftContent`;
`parse_and_ground` parses that JSON (tolerating a stray markdown code fence), validates it against
the strict `SarDraftContent` schema (`extra="forbid"`), and then GROUNDS it — `ground_citations`
keeps only the citation ids the model was actually given (in `SarInput.citations`), so a
fabricated or hallucinated regulation id can never reach the persisted SAR (the "no fabricated
ids" guardrail, plan §8.1). `render_markdown` turns the validated body into the human-readable SAR
text that is persisted to `sar_drafts.content`, run through the deterministic core masker so the
stored/displayed text is PHI-safe even if the model echoed a PHI-shaped span. All pure functions —
no IO, no provider calls.

Key classes:
- SarSchemaError: raised when model output is not valid against the SAR schema.

Key functions:
- ground_citations: keep only the claimed citation ids that were actually provided (drop the rest).
- parse_content: parse + validate model JSON into an ungrounded SarDraftContent.
- parse_and_ground: compose parsing and grounding into a SarDraftContent + its citations.
- render_markdown: render a validated SAR body into PHI-masked, human-readable markdown.

Notes:
- Grounding preserves the model's citation order and de-duplicates; the returned citation objects
  come from the trusted `available` list, never from the model, so titles/snippets are never forged.
- `parse_and_ground` strips a single leading/trailing fenced code block (``` or ```json) before
  parsing, since models often wrap JSON despite the instruction not to.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import ValidationError

from fraudlens_core.phi import mask_text
from fraudlens_ml.sar import SarCitation, SarDraftContent

_CODE_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n(?P<body>.*?)\n```\s*$", re.DOTALL | re.IGNORECASE
)


class SarSchemaError(ValueError):
    """Raised when model output cannot be parsed/validated as a SAR draft body."""


def ground_citations(
    claimed: Sequence[str], available: Sequence[SarCitation]
) -> tuple[tuple[str, ...], tuple[SarCitation, ...]]:
    """Keep only claimed ids that were actually provided; return grounded ids + their citations."""
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

"""Summary: Citation extraction + the RAG-as-data injection defense (plan §8.1, §16 Phase 6).
Retrieved regulatory text is UNTRUSTED input to the SAR-drafting prompt: a poisoned corpus chunk
could try to smuggle instructions ("ignore previous instructions…"). `escape_as_data` neutralizes
that by stripping control characters and escaping the angle brackets used by prompt/markup
delimiters, so no chunk can forge a prompt delimiter or inject markup. `extract_citations` turns
retrieved chunks into the deduplicated, ordered citation list surfaced to the analyst, persisted on
the run (the audit trail: which regulation grounded which SAR), and rendered into the drafting
prompt. Pure functions — no IO, no network.

Escaped citations are the ONLY regulation carrier. Release 0.5.0 removed the second, pre-rendered
block (`SarInput.rag_context` and the helper that built it): production had stopped rendering it,
so it was an unread copy of the same text that could only drift. The prompt now delimits the
snippets itself, and the model egress policy refuses any snippet whose digest is not a committed
corpus chunk — so an uncommitted excerpt is REFUSED rather than merely fenced.

Key classes:
- Citation: one deduplicated regulatory citation with an escaped supporting snippet.

Key functions:
- escape_as_data: neutralize a snippet so it is inert reference data, never instructions.
- extract_citations: dedupe retrieved chunks into an ordered, escaped citation list.

Notes:
- The drafting prompt delimits each snippet with '<' / '>' markup; since `escape_as_data`
  escapes those characters in every snippet, no chunk content can close the delimiter early or
  break out of the data block.
- `extract_citations` preserves first-seen order and dedupes by citation, so the same provision
  retrieved in multiple chunks is cited once (stable, audit-friendly output).
- Snippets are length-capped so a large chunk cannot blow the prompt/budget; the cap is a caller
  argument (config-driven upstream), never a hidden constant.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_ml.rag.retriever import RetrievedChunk

DEFAULT_SNIPPET_CHARS = 600
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_WHITESPACE_RE = re.compile(r"\s+")
_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))


class Citation(BaseModel):
    """One deduplicated regulatory citation with its escaped, length-capped supporting snippet."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    citation: str = Field(..., description="Exact regulatory citation (e.g. '31 CFR 1010.314').")
    title: str = Field(..., description="Title of the cited provision.")
    source: str = Field(..., description="Publisher of the provision (e.g. FinCEN / BSA).")
    snippet: str = Field(..., description="Escaped, capped supporting text (injection-safe).")


def escape_as_data(text: str, *, max_chars: int = DEFAULT_SNIPPET_CHARS) -> str:
    """Neutralize a snippet into inert reference data: strip control chars, escape markup, cap."""
    collapsed = _WHITESPACE_RE.sub(" ", _CONTROL_CHARS_RE.sub(" ", text)).strip()
    for raw, escaped in _ESCAPES:
        collapsed = collapsed.replace(raw, escaped)
    if len(collapsed) > max_chars:
        collapsed = collapsed[:max_chars].rstrip() + "…"
    return collapsed


def extract_citations(
    chunks: Sequence[RetrievedChunk], *, max_chars: int = DEFAULT_SNIPPET_CHARS
) -> list[Citation]:
    """Dedupe retrieved chunks (by citation, first-seen order) into escaped Citations."""
    citations: list[Citation] = []
    seen: set[str] = set()
    for chunk in chunks:
        if not chunk.citation or chunk.citation in seen:
            continue
        seen.add(chunk.citation)
        citations.append(
            Citation(
                citation=chunk.citation,
                title=chunk.title,
                source=chunk.source,
                snippet=escape_as_data(chunk.text, max_chars=max_chars),
            )
        )
    return citations

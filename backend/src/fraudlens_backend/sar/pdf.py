"""Summary: SAR PDF rendering + deferred, retried generation (plan §16 Phase 9: "approve → PDF
(Blob/local, deferred/retried — never blocks approval)"). `render_sar_pdf` builds a minimal, valid,
single-page PDF from a draft's already-masked narrative with **zero dependencies** (a hand-rolled
PDF-1.4 document — no reportlab/spaCy weight in the ≤75s cold-start budget). `generate_sar_pdf`
is the background task the approve handler schedules: it opens its own short-lived session, renders
the PDF, stores it through the configured `StorageBackend` (local-FS in the demo, Azure Blob later),
and records the URI on the `sar_drafts` row — retried on transient failure; idempotent if the URI
is already set. Because it runs after the response, SAR approval succeeds regardless of
whether (or when) the PDF lands. Keys + content are PHI-free (ids only in the key; the narrative is
masked upstream).

Key classes:
- (none)

Key functions:
- render_sar_pdf: build a minimal valid single-page PDF (bytes) from masked SAR fields.
- sar_pdf_key: the PHI-free storage key for a draft's PDF (`sar/<agencyId>/<draftId>.pdf`).
- generate_sar_pdf: the deferred + bounded-retry task that renders, stores, and records the PDF.

Notes:
- The renderer lays text out top-down on a US-Letter page (Helvetica, a standard-14 font that needs
  no embedding) and caps lines to one page; the authoritative SAR is the `sar_drafts` row, so the
  PDF is a portable rendering, not the system of record.
- Byte offsets for the xref table are computed over a latin-1 1:1 sanitized string, so the emitted
  bytes and the offset math agree exactly (non-latin-1 glyphs degrade to '?').
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.backends.storage import StorageBackend
from fraudlens_backend.db.repositories import SarDraftRepository
from fraudlens_backend.middleware.logging import APP_LOGGER_NAME, get_logger

# US-Letter page geometry + text layout (points; PDF origin is bottom-left).
_PAGE_WIDTH = 612
_PAGE_HEIGHT = 792
_MARGIN_X = 54
_TOP_Y = 738
_FONT_SIZE = 10
_LEADING = 14
_WRAP_COLS = 92  # characters per line before wrapping (monospace-ish budget for Helvetica 10pt)
_MAX_LINES = (_TOP_Y - 54) // _LEADING  # lines that fit above the bottom margin


def _latin1(text: str) -> str:
    """Return text restricted to a latin-1 1:1 representation (so offsets == byte offsets)."""
    return text.encode("latin-1", "replace").decode("latin-1")


def _escape(text: str) -> str:
    """Escape a PDF literal string's special characters (backslash + parentheses)."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap(text: str, *, width: int = _WRAP_COLS) -> list[str]:
    """Wrap a paragraph to `width` columns, preserving explicit newlines (blank lines kept)."""
    lines: list[str] = []
    for raw in text.split("\n"):
        if not raw:
            lines.append("")
            continue
        current = ""
        for word in raw.split(" "):
            candidate = f"{current} {word}".strip()
            if len(candidate) > width and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines


def _layout_lines(
    *, draft_id: str, run_id: str, status: str, content: str, citations: list[str]
) -> list[str]:
    """Assemble the ordered, sanitized text lines of the SAR PDF (header + body + citations)."""
    lines = [
        "SUSPICIOUS ACTIVITY REPORT (DRAFT)",
        "",
        f"SAR draft id: {draft_id}",
        f"Investigation run: {run_id}",
        f"Status: {status}",
        "",
        "Narrative",
        "---------",
    ]
    lines.extend(_wrap(content))
    if citations:
        lines.extend(["", "Regulatory citations", "--------------------"])
        lines.extend(f"- {citation}" for citation in citations)
    sanitized = [_latin1(line) for line in lines]
    if len(sanitized) > _MAX_LINES:
        sanitized = [*sanitized[: _MAX_LINES - 1], "... (truncated; see system of record)"]
    return sanitized


def _content_stream(lines: Iterable[str]) -> str:
    """Build the page content stream that draws the lines top-down (BT/ET + leading + T*)."""
    body = [f"BT /F1 {_FONT_SIZE} Tf {_MARGIN_X} {_TOP_Y} Td {_LEADING} TL"]
    for index, line in enumerate(lines):
        if index:
            body.append("T*")
        body.append(f"({_escape(line)}) Tj")
    body.append("ET")
    return "\n".join(body)


def render_sar_pdf(
    *, draft_id: str, run_id: str, status: str, content: str, citations: list[str]
) -> bytes:
    """Render a minimal valid single-page PDF (bytes) from a masked SAR draft's fields."""
    stream = _content_stream(
        _layout_lines(
            draft_id=draft_id, run_id=run_id, status=status, content=content, citations=citations
        )
    )
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_WIDTH} {_PAGE_HEIGHT}] "
            "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    document = "%PDF-1.4\n"
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document += f"{index} 0 obj\n{body}\nendobj\n"
    xref_offset = len(document)
    document += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    document += "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    document += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    )
    return document.encode("latin-1")


def sar_pdf_key(agency_id: uuid.UUID, draft_id: uuid.UUID) -> str:
    """Return the PHI-free storage key for a draft's PDF (ids only, no account data)."""
    return f"sar/{agency_id}/{draft_id}.pdf"


def _citation_labels(citations: list[object]) -> list[str]:
    """Extract the PHI-free citation labels from a draft's stored citation blobs."""
    labels: list[str] = []
    for citation in citations:
        if isinstance(citation, dict):
            label = citation.get("citation") or citation.get("title")
            if label:
                labels.append(str(label))
    return labels


async def generate_sar_pdf(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: StorageBackend,
    agency_id: uuid.UUID,
    draft_id: uuid.UUID,
    max_attempts: int,
) -> bool:
    """Render + store the SAR PDF and record its URI, bounded-retrying; True on success.

    Runs deferred (after the approve response) so it never blocks approval; idempotent when the URI
    is already set; a total failure is logged PHI-free and leaves approval untouched (plan §16 P9).
    """
    for _attempt in range(max_attempts):
        try:
            async with sessionmaker() as session:
                repo = SarDraftRepository(session, agency_id)
                draft = await repo.get(draft_id)
                if draft is None:
                    return False
                if draft.pdf_blob_url:
                    return True  # already generated — idempotent re-entry
                pdf = render_sar_pdf(
                    draft_id=str(draft.id),
                    run_id=str(draft.run_id),
                    status=draft.status.value,
                    content=draft.content,
                    citations=_citation_labels(list(draft.citations or [])),
                )
                url = storage.put(sar_pdf_key(agency_id, draft_id), pdf)
                await repo.set_pdf_url(draft_id, url)
                await session.commit()
            return True
        except Exception:  # transient store/DB failure → retry; never crash the worker (PHI-free)
            get_logger(APP_LOGGER_NAME).warning(
                "sar.pdf_generation_retry", draft_id=str(draft_id), exc_info=True
            )
    get_logger(APP_LOGGER_NAME).error("sar.pdf_generation_failed", draft_id=str(draft_id))
    return False

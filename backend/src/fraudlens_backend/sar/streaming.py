"""Summary: Shared safe-output streaming for the SAR drafters (plan §10.2, Phase 8).
Both drafters emit a finished, validated `SarDraftResult` through one event helper: a successful
draft yields its PHI-masked content as deltas followed by one terminal `COMPLETED` event; a failed
draft yields one terminal `FAILED` event. The live drafter now consumes a genuine provider stream
server-side, but its structured JSON must be fully parsed, citation-grounded, and guardrail-scanned
before disclosure. This helper therefore chunks only that validated rendering; it never exposes
raw provider deltas or partial JSON.

Key classes:
- (none)

Key functions:
- stream_result: yield token events for a completed draft, then its terminal completed/failed event.

Notes:
- Token chunks keep their trailing whitespace, so concatenating the deltas reproduces the content.
- Live provider transport is native streaming; browser-facing deltas intentionally begin only
  after full validation. A future two-stage narrative/citation schema would be required for safe
  pre-terminal browser delivery.
- The async-generator shape matches the `SarDrafter.draft` contract, so a drafter just delegates
  its tail to this helper with `async for ... yield`.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterator

from fraudlens_ml.sar import SarDraftResult, SarDraftStatus, SarEventType, SarStreamEvent

_TOKEN_RE = re.compile(r"\S+\s*")


def _chunk_tokens(text: str) -> Iterator[str]:
    """Yield word-sized token deltas (each keeping its trailing whitespace)."""
    yield from _TOKEN_RE.findall(text)


async def stream_result(result: SarDraftResult) -> AsyncIterator[SarStreamEvent]:
    """Stream only a validated draft's rendered tokens, then its terminal event."""
    if result.status == SarDraftStatus.DRAFT:
        for token in _chunk_tokens(result.content):
            yield SarStreamEvent(type=SarEventType.TOKEN, token=token)
        yield SarStreamEvent(type=SarEventType.COMPLETED, result=result)
        return
    yield SarStreamEvent(type=SarEventType.FAILED, result=result)

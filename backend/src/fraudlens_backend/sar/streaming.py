"""Summary: Shared token-streaming for the SAR drafters (plan §10.2 "loop tokens", §16 Phase 7).
Both the mock and the live drafter produce a finished, validated `SarDraftResult` and then stream
it the same way, so that logic lives here once (no duplication, rule 5): a successful draft streams
its PHI-masked content as token deltas followed by a single terminal `COMPLETED` event carrying the
result; a failed draft streams no tokens, just one terminal `FAILED` event (the run still completes
with score+SHAP+RAG, plan §7.5). The live client has no native token stream, so the completed text
is re-chunked into word-sized deltas — enough for the live SSE typing effect while keeping the
authoritative content in the persisted result.

Key classes:
- (none)

Key functions:
- stream_result: yield token events for a completed draft, then its terminal completed/failed event.

Notes:
- Token chunks keep their trailing whitespace, so concatenating the deltas reproduces the content.
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
    """Stream a completed draft's tokens then its terminal completed/failed event."""
    if result.status == SarDraftStatus.DRAFT:
        for token in _chunk_tokens(result.content):
            yield SarStreamEvent(type=SarEventType.TOKEN, token=token)
        yield SarStreamEvent(type=SarEventType.COMPLETED, result=result)
        return
    yield SarStreamEvent(type=SarEventType.FAILED, result=result)

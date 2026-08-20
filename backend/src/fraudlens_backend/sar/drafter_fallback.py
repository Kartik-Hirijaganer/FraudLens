"""Summary: Live-only fallback from the bounded agent workflow to the existing single writer.
The adapter forwards agent lifecycle events, suppresses only an unrecoverable agent terminal
failure, and then delegates to `LiveSarDrafter`; budget denials and successful drafts never fall
back.

Key classes:
- LiveAgentFallbackDrafter: use the live single writer after an unrecoverable agent fault.

Key functions:
- (none)

Notes:
- This adapter is never constructed in mock mode, so a live failure cannot silently become mock.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fraudlens_backend.sar.drafter_mock import MockSarDrafter
from fraudlens_ml.sar import SarDrafter, SarEventType, SarInput, SarStreamEvent


class LiveAgentFallbackDrafter:
    """Fall back to the configured live single writer after an unrecoverable graph failure."""

    def __init__(self, *, primary: SarDrafter, fallback: SarDrafter) -> None:
        """Bind the multi-agent primary and live-only fallback drafters."""
        if isinstance(fallback, MockSarDrafter):
            raise TypeError("Multi-agent fallback must never use the mock drafter")
        self._primary = primary
        self._fallback = fallback

    async def draft(self, sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        """Forward the primary unless its terminal result is eligible for live fallback."""
        use_fallback = False
        async for event in self._primary.draft(sar_input):
            if (
                event.type is SarEventType.FAILED
                and event.result is not None
                and event.result.workflow == "multi_agent"
            ):
                use_fallback = True
                continue
            yield event
        if use_fallback:
            async for event in self._fallback.draft(sar_input):
                yield event

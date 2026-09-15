"""Summary: Restart-safe SAR drafter backed by an already persisted successful draft.

Key classes:
- PersistedSarDrafter: replay one durable machine draft without another provider call.

Key functions:
- resume_drafter: select durable replay only for a successful machine draft.

Notes:
- Only machine drafts in `draft` state are eligible; failed or human-reviewed versions are never
  treated as a provider-call replay.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fraudlens_backend.db.models import SarDraft, SarStatus
from fraudlens_ml.sar import (
    SarCitation,
    SarDraftContent,
    SarDrafter,
    SarDraftResult,
    SarDraftStatus,
    SarEventType,
    SarInput,
    SarStreamEvent,
    SarTokenUsage,
)


class PersistedSarDrafter:
    """Yield one cached terminal result reconstructed from a durable successful draft."""

    def __init__(self, draft: SarDraft) -> None:
        """Validate and retain the persisted draft as a typed pipeline result."""
        self._result = SarDraftResult(
            status=SarDraftStatus.DRAFT,
            content=draft.content,
            structured=(
                SarDraftContent.model_validate(draft.structured) if draft.structured else None
            ),
            citations=tuple(SarCitation.model_validate(item) for item in draft.citations),
            model_id=draft.model_id,
            prompt_version=draft.prompt_version,
            prompt_hash=draft.prompt_hash,
            token_usage=SarTokenUsage.model_validate(draft.token_usage),
            cost_usd=draft.cost_usd,
            cached=True,
            workflow=draft.workflow,
            revision_count=draft.revision_count,
        )

    async def draft(self, _sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        """Replay the persisted terminal draft with no token stream or provider access."""
        yield SarStreamEvent(type=SarEventType.COMPLETED, result=self._result)


def resume_drafter(primary: SarDrafter, draft: SarDraft | None) -> SarDrafter:
    """Return a persisted replay drafter only when a successful machine draft exists."""
    return (
        PersistedSarDrafter(draft)
        if draft is not None and draft.status is SarStatus.DRAFT
        else primary
    )

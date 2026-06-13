"""Summary: The agency-scoped SAR draft repository (plan §9.1 `sar_drafts`, §16 Phase 7). Built on
`TenantScopedRepository`, so every read/write is bound to one `agency_id` (a cross-tenant run/alert
id resolves to nothing). `create_from_result` is the single persistence path the pipeline uses: it
maps a PHI-free `SarDraftResult` onto a `sar_drafts` row — the masked `content`, the
structured body + grounded citations (stored as their camelCase JSON), the model/prompt provenance,
and the token-usage + USD cost audit fields (plan §7.4) — auto-assigning the next `version` for the
run so a re-draft (e.g. after a transient provider failure) is recorded, not overwritten. A
`failed` result persists too (empty content, `status=failed`), so a run that lost its SAR still has
the auditable attempt (plan §7.5).

Key classes:
- SarDraftRepository: agency-scoped persistence + lookup for the `sar_drafts` table.

Key functions:
- (none)

Notes:
- `structured` / `citations` / `token_usage` are dumped `mode="json"` + `by_alias`, so the persisted
  JSON is camelCase and JSON-native (tuples→arrays) — matching the API surface with no remapping.
- `get_for_run` returns the latest version for a run; `list_for_alert` returns an alert's drafts
  newest-first — both agency-scoped so cross-tenant rows are never returned (defense-in-depth).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import SarDraft
from fraudlens_backend.db.models.enums import SarStatus
from fraudlens_backend.db.repositories.base import TenantScopedRepository
from fraudlens_ml.sar import SarDraftResult


class SarDraftRepository(TenantScopedRepository[SarDraft]):
    """Agency-scoped persistence + lookup for the `sar_drafts` table."""

    def __init__(self, session: AsyncSession, agency_id: uuid.UUID) -> None:
        """Bind the session + agency scope to the `sar_drafts` table."""
        super().__init__(session, SarDraft, agency_id)

    async def create_from_result(
        self,
        *,
        run_id: uuid.UUID,
        result: SarDraftResult,
        alert_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
    ) -> SarDraft:
        """Persist a SarDraftResult as the next `sar_drafts` version for the run (agency-scoped)."""
        structured = (
            result.structured.model_dump(by_alias=True, mode="json")
            if result.structured is not None
            else {}
        )
        draft = SarDraft(
            agency_id=self._agency_id,
            run_id=run_id,
            alert_id=alert_id,
            version=await self._next_version(run_id),
            model_id=result.model_id,
            prompt_version=result.prompt_version,
            prompt_hash=result.prompt_hash,
            content=result.content,
            structured=structured,
            citations=[
                citation.model_dump(by_alias=True, mode="json") for citation in result.citations
            ],
            status=SarStatus(result.status.value),
            token_usage=result.token_usage.model_dump(by_alias=True, mode="json"),
            cost_usd=result.cost_usd,
            created_by=created_by,
        )
        self._session.add(draft)
        await self._session.flush()
        return draft

    async def get_for_run(self, run_id: uuid.UUID) -> SarDraft | None:
        """Return this agency's latest SAR draft (highest version) for a run, or None."""
        stmt = (
            select(SarDraft)
            .where(SarDraft.agency_id == self._agency_id, SarDraft.run_id == run_id)
            .order_by(SarDraft.version.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_alert(self, alert_id: uuid.UUID) -> Sequence[SarDraft]:
        """Return this agency's SAR drafts for an alert, newest version first."""
        stmt = (
            select(SarDraft)
            .where(SarDraft.agency_id == self._agency_id, SarDraft.alert_id == alert_id)
            .order_by(SarDraft.version.desc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def _next_version(self, run_id: uuid.UUID) -> int:
        """Return the next monotonic draft version for a run (1 when none exist yet)."""
        stmt = select(func.max(SarDraft.version)).where(
            SarDraft.agency_id == self._agency_id, SarDraft.run_id == run_id
        )
        current = (await self._session.execute(stmt)).scalar_one_or_none()
        return (current or 0) + 1

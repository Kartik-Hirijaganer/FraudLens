"""Summary: The API-surface representation of a SAR draft (plan §5.3, §16 Phase 7). `SarDraftView`
is the camelCase `CamelModel` projection of a persisted `sar_drafts` row, surfaced to the analyst
UI through the alert-detail endpoint (built in Phase 9 — Phase 7 ships no standalone SAR route).
It carries the masked narrative, the structured body + grounded citations, the model/prompt
    provenance (`modelId`, `promptVersion`, `promptHash`, `workflow`, `revisionCount`), and the
    cost/token audit fields (plan §7.4), plus the human-review `status`. It reuses the canonical
    `SarStatus` enum (no duplicated vocabulary,
rule 5) and is PHI-free: `content` is the masked narrative and the structured/citation blobs carry
only PHI-free fields.

Key classes:
- SarDraftView: the camelCase API projection of a persisted SAR draft.

Key functions:
- (none)

Notes:
- `structured` / `citations` / `tokenUsage` are passthrough JSON blobs already stored camelCase on
  the row (`SarDraftContent` / `SarCitation` / `SarTokenUsage` dumped by alias), so the wire shape
  matches the SAR schema without a second mapping.
- The ORM→view mapping lives in the (Phase 9) handler, mirroring the model-registry API convention
  (the boundary model stays import-pure of the ORM).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import ConfigDict, Field

from fraudlens_backend.db.models.enums import SarStatus
from fraudlens_backend.models.common import CamelModel


class SarDraftView(CamelModel):
    """The camelCase API projection of a persisted SAR draft (PHI-free, human-reviewed)."""

    # `model_id` is a deliberate API field name (matches sar_drafts.model_id); opt out of
    # pydantic's `model_` protected namespace so it does not warn (merges with CamelModel config).
    model_config = ConfigDict(protected_namespaces=())

    sar_draft_id: str = Field(..., description="The SAR draft's unique id (UUID).")
    run_id: str = Field(..., description="The analysis run that produced the draft.")
    alert_id: str | None = Field(
        default=None, description="The alert this draft is attached to, if any."
    )
    version: int = Field(..., ge=1, description="Monotonic draft version for the run.")
    status: SarStatus = Field(..., description="Human-review lifecycle status.")
    content: str = Field(..., description="The PHI-masked, human-readable SAR narrative.")
    structured: dict[str, Any] = Field(
        default_factory=dict, description="The structured SAR body (camelCase, PHI-free)."
    )
    citations: list[dict[str, Any]] = Field(
        default_factory=list, description="The grounded regulatory citations the SAR relied on."
    )
    model_id: str = Field(..., description="Model reference that produced the draft (or 'mock').")
    prompt_version: str = Field(..., description="SAR prompt template version id used.")
    prompt_hash: str = Field(..., description="Hash of the exact prompt template used.")
    workflow: str = Field(..., description="Drafting workflow that produced this artifact.")
    revision_count: int = Field(
        ..., ge=0, description="Number of agent-writer revisions completed for this artifact."
    )
    token_usage: dict[str, Any] = Field(
        default_factory=dict, description="Token usage recorded for the call (cost/audit trail)."
    )
    cost_usd: Decimal = Field(..., ge=0, description="Estimated USD spend for the draft.")
    created_at: datetime = Field(..., description="When the draft was created.")

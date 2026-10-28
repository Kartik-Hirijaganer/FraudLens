"""Summary: CamelCase alert summaries, masked activity and authoritative case workspace responses.

Key classes:
- AlertView: case ownership, current flags, version and backend capabilities.
- AlertActionView: masked activity with optional historical person and submission references.
- AlertDetailResponse: evidence, immutable submission rounds and preserved SAR versions.
- AlertListResponse: tenant-authorized filtered case queue.
- AlertActionRequest: self pickup, meaningful comments and governed dismissal only.

Key functions:
- (none)

Notes:
- Preparation and checker commands are defined in models/case_workflow.py.
- Historical identity is nullable; reasons are read from masked action notes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from fraudlens_backend.db.models.enums import (
    AlertActionType,
    AlertOrigin,
    AlertStatus,
    Severity,
)
from fraudlens_backend.models.agent_executions import AgentExecutionView
from fraudlens_backend.models.case_workflow import (
    ActionCapability,
    CaseVersionRequest,
    SubmissionView,
)
from fraudlens_backend.models.common import CamelModel
from fraudlens_backend.models.sar import SarDraftView

_MAX_NOTE_LEN = 2_000
_MAX_REASON_LEN = 2_000
_MAX_EDITED_CONTENT_LEN = 20_000


class AlertView(CamelModel):
    """The camelCase summary projection of a persisted alert (list + detail header)."""

    alert_id: str = Field(..., description="The alert's unique id (UUID).")
    transaction_id: str = Field(..., description="The flagged transaction's id.")
    run_id: str = Field(..., description="The investigation run that raised the alert.")
    origin: AlertOrigin = Field(
        ..., description="Alert provenance: pipeline output or explicitly seeded sample data."
    )
    status: AlertStatus = Field(
        ...,
        description=(
            "Lifecycle status (open|pending_review|in_review|escalated|resolved|dismissed)."
        ),
    )
    severity: Severity = Field(..., description="Alert severity derived from the run's risk band.")
    amount: Decimal = Field(..., description="Amount from the linked flagged transaction.")
    currency: str = Field(..., min_length=3, max_length=3, description="Linked ISO-4217 currency.")
    assigned_to: str | None = Field(
        default=None, description="User id the alert is currently assigned to, if any."
    )
    assigned_to_name: str | None = Field(
        default=None,
        description=(
            "Display name of the assigned user when that user belongs to the alert's agency."
        ),
    )
    review_flags: list[dict[str, str]] = Field(
        default_factory=list,
        description="PHI-free force-review reasons ({flag, reason}) set at investigation time.",
    )
    case_version: int = Field(..., description="Optimistic concurrency version.")
    owner_person_id: str | None = Field(default=None, description="Canonical maker identity.")
    capabilities: dict[str, ActionCapability] = Field(
        default_factory=dict, description="Authoritative action availability and reasons."
    )
    submission_status: str | None = Field(
        default=None, description="Current or most recent submission status."
    )
    created_at: datetime = Field(..., description="When the alert was raised.")
    updated_at: datetime = Field(..., description="When the alert was last updated.")


class AlertActionView(CamelModel):
    """One append-only triage action projected onto the API surface (PHI-masked note)."""

    action_id: str = Field(..., description="The action's unique id (UUID).")
    action: AlertActionType = Field(..., description="The triage action recorded.")
    actor_id: str = Field(..., description="User id that performed the action.")
    actor_person_id: str | None = Field(
        default=None, description="Historical person identity when recorded."
    )
    submission_id: str | None = Field(default=None, description="Related submission round.")
    note: str | None = Field(default=None, description="Optional PHI-masked free-text note.")
    from_status: str | None = Field(default=None, description="Alert status before the action.")
    to_status: str | None = Field(default=None, description="Alert status after the action.")
    created_at: datetime = Field(..., description="When the action was recorded.")


class AlertDetailResponse(CamelModel):
    """An alert with its SAR, run workflow trace, and append-only action history."""

    alert: AlertView = Field(..., description="The alert summary.")
    sar_draft: SarDraftView | None = Field(
        default=None, description="The latest SAR draft for the alert's run, if one exists."
    )
    actions: list[AlertActionView] = Field(
        default_factory=list, description="The alert's triage actions, newest first."
    )
    agent_executions: list[AgentExecutionView] = Field(
        default_factory=list,
        description="Agent execution trace, present even when multi-agent drafting failed.",
    )
    workflow_mode: str = Field(..., description="Resolved drafting workflow persisted on the run.")
    graph_version: str | None = Field(
        default=None, description="Agent graph version, or null for single-writer runs."
    )
    revision_count: int = Field(
        default=0, ge=0, description="Number of revisions in the latest SAR draft."
    )
    submissions: list[SubmissionView] = Field(
        default_factory=list, description="Frozen rounds, newest first."
    )
    draft_versions: list[SarDraftView] = Field(
        default_factory=list, description="Preserved working and submitted artifacts."
    )
    sar_content: str | None = Field(
        default=None, description="Latest persisted SAR content, if one exists."
    )


class AlertListResponse(CamelModel):
    """A page of the agency's alerts (newest first)."""

    alerts: list[AlertView] = Field(
        default_factory=list, description="The agency's alerts for the requested page/filter."
    )
    total: int = Field(
        default=0, ge=0, description="All cases matching the caller's filters before pagination."
    )


class AlertActionRequest(CaseVersionRequest):
    """Only self-assignment, meaningful comments and governed dismissal remain callable."""

    action: Literal["assign", "comment", "dismiss"] = Field(..., description="Case action.")
    assignee_id: uuid.UUID | None = Field(
        default=None,
        description="Optional self account id; assigning another account is forbidden.",
    )
    note: str | None = Field(
        default=None,
        max_length=_MAX_NOTE_LEN,
        description="Required rationale for dismissal or a nonblank comment.",
    )

    @model_validator(mode="after")
    def required_note(self) -> AlertActionRequest:
        """Reject blank comments and unexplained dismissals before writes."""
        if self.action in {"comment", "dismiss"} and not (self.note and self.note.strip()):
            raise ValueError("A nonblank note is required")
        return self

"""Summary: Pydantic request/response models for the alerts & review surface (plan §5.4, §10.4,
§16 Phase 9; endpoints 9-12). Every model is a `CamelModel`, so the wire is camelCase while Python
stays snake_case and `extra="forbid"` rejects unknown fields. Status/severity/action/label reuse the
canonical enums (no duplicated vocabularies, rule 5). `AlertActionRequest` is the triage body whose
conditional requirements (assign needs an assignee, resolve needs a label) are enforced by a
`model_validator` so a bad combination fails as a 422 envelope before any handler logic.
`SarReviewRequest` is the SAR decision body (approve / reject / edit) with reject-needs-reason and
edit-needs-content enforced the same way. `note` / `editedContent` / `reason` are length-bounded and
PHI-masked by the handler before persistence (plan §5.4 "note ≤2k, PHI-masked").

Key classes:
- SarReviewDecision: the SAR review verb (approve | reject | edit).
- AlertView: the camelCase summary projection of an alert (list + detail header).
- AlertActionView: one append-only triage action projected onto the API surface.
- AlertDetailResponse: an alert with its latest SAR draft + action history.
- AlertListResponse: a page of the agency's alerts.
- AlertActionRequest: the POST /alerts/{id}/actions body (assign/comment/escalate/resolve/dismiss).
- SarReviewRequest: the POST /alerts/{id}/sar/review body (approve/reject/edit).

Key functions:
- (none)

Notes:
- `reviewFlags` are PHI-free {flag, reason} pairs computed at investigation time (§8.5), surfaced
  so the UI can show why an alert was force-flagged for review.
- The conditional validators raise `ValueError`, which the request-validation handler renders as a
  422 with a field/message detail — never echoing the submitted note/reason value (PHI hygiene).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from fraudlens_backend.db.models.enums import (
    AlertActionType,
    AlertOrigin,
    AlertStatus,
    Severity,
    TrainingLabelType,
)
from fraudlens_backend.models.common import CamelModel
from fraudlens_backend.models.sar import SarDraftView

_MAX_NOTE_LEN = 2_000
_MAX_REASON_LEN = 2_000
_MAX_EDITED_CONTENT_LEN = 20_000


class SarReviewDecision(StrEnum):
    """The human SAR review verb applied to the latest draft (plan §5.4 / §10.4)."""

    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"


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
    review_flags: list[dict[str, str]] = Field(
        default_factory=list,
        description="PHI-free force-review reasons ({flag, reason}) set at investigation time.",
    )
    created_at: datetime = Field(..., description="When the alert was raised.")
    updated_at: datetime = Field(..., description="When the alert was last updated.")


class AlertActionView(CamelModel):
    """One append-only triage action projected onto the API surface (PHI-masked note)."""

    action_id: str = Field(..., description="The action's unique id (UUID).")
    action: AlertActionType = Field(..., description="The triage action recorded.")
    actor_id: str = Field(..., description="User id that performed the action.")
    note: str | None = Field(default=None, description="Optional PHI-masked free-text note.")
    from_status: str | None = Field(default=None, description="Alert status before the action.")
    to_status: str | None = Field(default=None, description="Alert status after the action.")
    created_at: datetime = Field(..., description="When the action was recorded.")


class AlertDetailResponse(CamelModel):
    """An alert with its latest SAR draft and full append-only action history."""

    alert: AlertView = Field(..., description="The alert summary.")
    sar_draft: SarDraftView | None = Field(
        default=None, description="The latest SAR draft for the alert's run, if one exists."
    )
    actions: list[AlertActionView] = Field(
        default_factory=list, description="The alert's triage actions, newest first."
    )


class AlertListResponse(CamelModel):
    """A page of the agency's alerts (newest first)."""

    alerts: list[AlertView] = Field(
        default_factory=list, description="The agency's alerts for the requested page/filter."
    )


class AlertActionRequest(CamelModel):
    """The POST /alerts/{id}/actions body; conditional fields validated up front (plan §5.4)."""

    action: AlertActionType = Field(..., description="The triage action to apply.")
    assignee_id: uuid.UUID | None = Field(
        default=None, description="Target user id for an `assign` action (must be in the agency)."
    )
    note: str | None = Field(
        default=None, max_length=_MAX_NOTE_LEN, description="Optional free-text note (PHI-masked)."
    )
    label: TrainingLabelType | None = Field(
        default=None, description="Outcome label for a `resolve` action (writes a training label)."
    )

    @model_validator(mode="after")
    def _check_action_requirements(self) -> AlertActionRequest:
        """Enforce assign-needs-assignee and resolve-needs-label (422 on violation)."""
        if self.action == AlertActionType.ASSIGN and self.assignee_id is None:
            raise ValueError("assigneeId is required for an assign action")
        if self.action == AlertActionType.RESOLVE and self.label is None:
            raise ValueError("label is required for a resolve action")
        return self


class SarReviewRequest(CamelModel):
    """The POST /alerts/{id}/sar/review body (approve/reject/edit), validated up front."""

    decision: SarReviewDecision = Field(..., description="The review verb to apply to the draft.")
    edited_content: str | None = Field(
        default=None,
        max_length=_MAX_EDITED_CONTENT_LEN,
        description="Edited SAR narrative (required for `edit`; optional final edit on `approve`).",
    )
    reason: str | None = Field(
        default=None,
        max_length=_MAX_REASON_LEN,
        description="Reason for the decision (required for `reject`; PHI-masked).",
    )

    @model_validator(mode="after")
    def _check_decision_requirements(self) -> SarReviewRequest:
        """Enforce reject-needs-reason and edit-needs-content (422 on violation)."""
        if self.decision == SarReviewDecision.REJECT and not (self.reason and self.reason.strip()):
            raise ValueError("reason is required to reject a SAR draft")
        if self.decision == SarReviewDecision.EDIT and not (
            self.edited_content and self.edited_content.strip()
        ):
            raise ValueError("editedContent is required to edit a SAR draft")
        return self

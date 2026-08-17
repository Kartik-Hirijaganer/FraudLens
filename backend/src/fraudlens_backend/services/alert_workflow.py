"""Summary: The alert & SAR review-workflow service (plan §5.4, §10.4) — the ONE implementation of
the review transition sequence, including its SAR-before-case-outcome guard. Extracted from
`api/v1/alerts.py` so the interactive routes and the
portfolio-demo bootstrap drive identical domain behavior instead of the bootstrap re-implementing it
(rule 5). One call performs the whole unit: tenant-scoped lookup, `next_alert_status` validation,
`user_in_agency` assignee validation, the append-only `alert_actions` row + resulting status, the
`training_labels` row a resolve/dismiss produces (matured by `load_label_maturity_days`), PHI
masking of reviewer free text, and the two PHI-free `audit_logs` rows (the counts-only mask record
plus the action record). The SAR path applies the same discipline to approve/reject and the
edit-creates-a-new-version route. Commands and results are frozen Pydantic models (rule 1) carrying
ids/enums only, so no caller has to know the ordering and no note or SAR body reaches an audit row.

Key classes:
- AlertActionCommand: the HTTP-free triage input (alert, actor, action, assignee, note, label).
- AlertActionResult: the PHI-free outcome of an applied triage action (ids + statuses).
- SarReviewCommand: the HTTP-free SAR review input (alert, actor, decision, edited content).
- SarReviewResult: the PHI-free outcome of a SAR review, carrying the resulting draft projection.
- AlertWorkflowService: applies triage actions and SAR review decisions for one tenant.

Key functions:
- sar_decision_allowed: pure check of whether a review decision is legal from a draft's status.

Notes:
- What deliberately stays OUTSIDE this service (plan §5 Phase 5): RBAC (`enforce_permission`),
  HTTP status mapping, and the deferred `generate_sar_pdf` background task. The service raises
  `AppError` codes the existing error registry already maps, so route status codes are unchanged.
- Nothing is committed here. Each method flushes and refreshes so the caller sees server-side
  `updated_at`, then the caller commits — the API after building its response, the bootstrap after
  its own aggregate job row. This mirrors `regenerate_sar_for_run`.
- The audit writer is INJECTED, not built: the API supplies the request-correlated writer
  (`audit_writer`), the bootstrap one correlated by the story's derived request id. The service
  therefore never touches `Request` and is usable from a script.
- `SarReviewResult` carries the projected `SarDraftView` because `sar_draft_to_view` is already the
  services layer's single ORM→view mapping (rule 5); the alternative — re-reading the row in the
  route — would add a query and an unreachable not-found branch for no gain.
- `AlertActionCommand` enforces assign-needs-assignee; the service additionally validates case
  closure against the latest tenant-scoped SAR decision before it writes a label or action.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import Alert, AlertActionType, AlertStatus
from fraudlens_backend.db.models.enums import LabelSource, SarStatus, TrainingLabelType
from fraudlens_backend.db.repositories import (
    AlertRepository,
    AuditLogRepository,
    SarDraftRepository,
)
from fraudlens_backend.db.repositories.alerts import load_label_maturity_days, next_alert_status
from fraudlens_backend.models.alerts import SarReviewDecision
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.models.sar import SarDraftView
from fraudlens_backend.services.sar_regeneration import sar_draft_to_view
from fraudlens_core.phi import MaskingReport, mask_text

# SAR statuses from which no further review decision is legal (the draft is decided).
_SAR_TERMINAL: frozenset[SarStatus] = frozenset({SarStatus.APPROVED, SarStatus.REJECTED})

# Audit `resource_type` values and the PHI-mask `source` tags the trail is filtered by.
_ALERT_RESOURCE = "alert"
_SAR_RESOURCE = "sar_draft"
_ACTION_NOTE_SOURCE = "alert.action_note"
_SAR_EDIT_SOURCE = "sar.review_edit"

# Closing labels must agree with the already-recorded SAR decision. `false_negative` belongs on
# the approved side because a deterministic rule may surface suspicious activity that the model
# probability under-scored; the SAR still documents a reportable concern.
_OUTCOME_LABELS_BY_SAR_STATUS: dict[SarStatus, frozenset[TrainingLabelType]] = {
    SarStatus.APPROVED: frozenset(
        {TrainingLabelType.CONFIRMED_FRAUD, TrainingLabelType.FALSE_NEGATIVE}
    ),
    SarStatus.REJECTED: frozenset({TrainingLabelType.FALSE_POSITIVE, TrainingLabelType.BENIGN}),
}


def sar_decision_allowed(status: SarStatus, decision: SarReviewDecision, *, has_edit: bool) -> bool:
    """Return whether a review decision is legal from the draft's current status (plan §10.4).

    A decided draft (approved/rejected) admits nothing further. A `failed` draft cannot be approved
    unless the reviewer supplies replacement content (an edit); reject/edit are legal otherwise.
    """
    if status in _SAR_TERMINAL:
        return False
    if decision == SarReviewDecision.APPROVE:
        return status != SarStatus.FAILED or has_edit
    return True


class AlertActionCommand(BaseModel):
    """One triage action to apply to an alert, independent of how it was requested."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_id: uuid.UUID = Field(..., description="Alert to act on (resolved within the tenant).")
    actor_id: uuid.UUID = Field(..., description="Verified acting user recorded on the action.")
    action: AlertActionType = Field(..., description="The triage action to apply.")
    assignee_id: uuid.UUID | None = Field(
        default=None, description="Assignment target for an `assign` (must be in the agency)."
    )
    note: str | None = Field(
        default=None, description="Optional free text; PHI-masked before persistence."
    )
    label: TrainingLabelType | None = Field(
        default=None, description="Outcome label a `resolve` writes as a training label."
    )

    @model_validator(mode="after")
    def _assign_needs_assignee(self) -> AlertActionCommand:
        """An assign without a target is a caller bug — reject it before any write."""
        if self.action is AlertActionType.ASSIGN and self.assignee_id is None:
            raise ValueError("an assign action requires an assignee id")
        return self


class AlertActionResult(BaseModel):
    """The PHI-free outcome of one applied triage action (ids/enums only — never the note)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_id: uuid.UUID = Field(..., description="The alert the action was applied to.")
    action_id: uuid.UUID = Field(..., description="The appended `alert_actions` row id.")
    action: AlertActionType = Field(..., description="The action that was applied.")
    from_status: AlertStatus = Field(..., description="Alert status before the action.")
    to_status: AlertStatus = Field(..., description="Alert status after the action.")
    assigned_to: uuid.UUID | None = Field(
        default=None, description="Assignee the alert now carries, if the action assigned one."
    )
    training_label_id: uuid.UUID | None = Field(
        default=None, description="The `training_labels` row a resolve/dismiss produced, if any."
    )


class SarReviewCommand(BaseModel):
    """One SAR review decision to apply to an alert's latest draft."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_id: uuid.UUID = Field(..., description="Alert whose latest draft is reviewed.")
    actor_id: uuid.UUID = Field(..., description="Verified reviewer recorded on the decision.")
    decision: SarReviewDecision = Field(..., description="approve | reject | edit.")
    edited_content: str | None = Field(
        default=None,
        description="Replacement narrative; a non-blank value creates a new masked version.",
    )


class SarReviewResult(BaseModel):
    """The outcome of one SAR review decision, with the resulting draft's API projection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_id: uuid.UUID = Field(..., description="The alert whose draft was reviewed.")
    run_id: uuid.UUID = Field(..., description="The analysis run the draft belongs to.")
    edited: bool = Field(..., description="True when the decision created a new draft version.")
    draft: SarDraftView = Field(..., description="The resulting draft (PHI-masked narrative).")


class AlertWorkflowService:
    """Applies alert triage actions and SAR review decisions for exactly one tenant."""

    def __init__(
        self, session: AsyncSession, *, agency_id: uuid.UUID, audit: AuditLogRepository
    ) -> None:
        """Bind the session, the agency scope, and the caller's correlated audit writer."""
        self._session = session
        self._alerts = AlertRepository(session, agency_id)
        self._sar = SarDraftRepository(session, agency_id)
        self._audit = audit

    async def apply_action(self, command: AlertActionCommand) -> AlertActionResult:
        """Apply a validated triage action as one unit (action row, label, audit); no commit.

        Raises `alert_not_found` (unknown/cross-tenant alert), `invalid_alert_transition` (the
        action is illegal from the current status), or `assignee_not_in_agency` (cross-tenant
        assignee). The alert is refreshed before returning so its server-side `updated_at` is
        loaded for the caller's projection.
        """
        alert = await self._alerts.get(command.alert_id)
        if alert is None:
            raise AppError("alert_not_found")
        target = next_alert_status(alert.status, command.action)
        if target is None:
            raise AppError("invalid_alert_transition")
        await self._validate_case_outcome(alert, command)
        from_status = alert.status
        assigned_to = await self._resolve_assignee(command)
        label_id = await self._write_training_label(alert, command)
        masked = mask_text(command.note) if command.note else None
        entry = await self._alerts.record_action(
            alert=alert,
            actor_id=command.actor_id,
            action=command.action,
            to_status=target,
            note=masked.value if masked is not None else None,
            assigned_to=assigned_to,
        )
        await self._record_text_phi_mask(
            actor_id=command.actor_id,
            resource_type=_ALERT_RESOURCE,
            resource_id=str(command.alert_id),
            report=masked.report if masked is not None else None,
            source=_ACTION_NOTE_SOURCE,
        )
        await self._audit.record(
            actor_id=command.actor_id,
            action=f"alert.{command.action.value}",
            resource_type=_ALERT_RESOURCE,
            resource_id=str(command.alert_id),
            metadata=_action_metadata(command, from_status=from_status, to_status=target),
        )
        # Refresh so the server-side `updated_at` (onupdate) is loaded before the caller projects
        # the row (else a post-commit attribute access would lazy-load in a sync context).
        await self._session.refresh(alert)
        return AlertActionResult(
            alert_id=command.alert_id,
            action_id=entry.id,
            action=command.action,
            from_status=from_status,
            to_status=target,
            assigned_to=assigned_to,
            training_label_id=label_id,
        )

    async def review_sar(self, command: SarReviewCommand) -> SarReviewResult:
        """Apply a SAR review decision to the alert's latest draft (masking + audit); no commit.

        Raises `alert_not_found`, `sar_draft_not_found` (the run produced no draft), or
        `invalid_sar_transition` (the decision is illegal from the draft's status). An edit is
        persisted as a NEW version rather than overwriting the machine draft.
        """
        alert = await self._alerts.get(command.alert_id)
        if alert is None:
            raise AppError("alert_not_found")
        draft = await self._sar.get_for_run(alert.run_id)
        if draft is None:
            raise AppError("sar_draft_not_found")
        has_edit = bool(command.edited_content and command.edited_content.strip())
        if not sar_decision_allowed(draft.status, command.decision, has_edit=has_edit):
            raise AppError("invalid_sar_transition")
        target = draft
        edited_report: MaskingReport | None = None
        if has_edit and command.edited_content is not None:
            masked_edit = mask_text(command.edited_content)
            edited_report = masked_edit.report
            target = await self._sar.create_edited_version(
                base=draft,
                content=masked_edit.value,
                created_by=command.actor_id,
                alert_id=alert.id,
            )
        if command.decision == SarReviewDecision.APPROVE:
            await self._sar.set_review_status(
                target, status=SarStatus.APPROVED, reviewed_by=command.actor_id
            )
        elif command.decision == SarReviewDecision.REJECT:
            await self._sar.set_review_status(
                target, status=SarStatus.REJECTED, reviewed_by=command.actor_id
            )
        await self._record_text_phi_mask(
            actor_id=command.actor_id,
            resource_type=_SAR_RESOURCE,
            resource_id=str(target.id),
            report=edited_report,
            source=_SAR_EDIT_SOURCE,
        )
        await self._audit.record(
            actor_id=command.actor_id,
            action=f"sar.{command.decision.value}",
            resource_type=_SAR_RESOURCE,
            resource_id=str(target.id),
            metadata={"decision": command.decision.value, "status": target.status.value},
        )
        return SarReviewResult(
            alert_id=command.alert_id,
            run_id=alert.run_id,
            edited=has_edit,
            draft=sar_draft_to_view(target),
        )

    async def _validate_case_outcome(self, alert: Alert, command: AlertActionCommand) -> None:
        """Require a decided SAR before closure and keep its final label consistent.

        Alerts without a SAR remain resolvable because degraded investigations may require a
        fully manual decision. A domain caller may omit a resolve label (the synthetic portfolio
        bootstrap does); HTTP reviewer requests already require one at the request boundary.
        """
        if command.action not in {AlertActionType.RESOLVE, AlertActionType.DISMISS}:
            return
        draft = await self._sar.get_for_run(alert.run_id)
        if draft is None:
            return
        allowed = _OUTCOME_LABELS_BY_SAR_STATUS.get(draft.status)
        if allowed is None:
            raise AppError("sar_decision_required")
        if command.action is AlertActionType.DISMISS:
            if draft.status is not SarStatus.REJECTED:
                raise AppError("resolution_label_mismatch")
            return
        if command.label is not None and command.label not in allowed:
            raise AppError("resolution_label_mismatch")

    async def _resolve_assignee(self, command: AlertActionCommand) -> uuid.UUID | None:
        """Return the agency-validated assignee for an `assign`, else None (403 cross-tenant)."""
        assignee_id = command.assignee_id
        # The command validator guarantees an assignee for assign, so a None here means the action
        # is not an assignment (an assignee carried by any other action is ignored, as before).
        if command.action is not AlertActionType.ASSIGN or assignee_id is None:
            return None
        if not await self._alerts.user_in_agency(assignee_id):
            raise AppError("assignee_not_in_agency")
        return assignee_id

    async def _write_training_label(
        self, alert: Alert, command: AlertActionCommand
    ) -> uuid.UUID | None:
        """Write the `training_labels` row a resolve/dismiss produces, else None (plan §10.4)."""
        if command.action == AlertActionType.RESOLVE and command.label is not None:
            label, source = command.label, LabelSource.ANALYST_REVIEW
        elif command.action == AlertActionType.DISMISS:
            label, source = TrainingLabelType.FALSE_POSITIVE, LabelSource.ANALYST_DISMISS
        else:
            return None
        maturity_days = await load_label_maturity_days(self._session)
        row = await self._alerts.create_training_label(
            transaction_id=alert.transaction_id,
            run_id=alert.run_id,
            label=label,
            source=source,
            created_by=command.actor_id,
            matured_at=datetime.now(UTC) + timedelta(days=maturity_days),
        )
        return row.id

    async def _record_text_phi_mask(
        self,
        *,
        actor_id: uuid.UUID,
        resource_type: str,
        resource_id: str,
        report: MaskingReport | None,
        source: str,
    ) -> None:
        """Record counts-only PHI masking for reviewer-entered free text (never the value)."""
        if report is None or report.total == 0:
            return
        await self._audit.record(
            actor_id=actor_id,
            action="phi_mask",
            resource_type=resource_type,
            resource_id=resource_id,
            metadata={
                "source": source,
                "maskedCount": str(report.total),
                "categories": ",".join(
                    f"{key}:{value}" for key, value in report.categories.items()
                ),
            },
        )


def _action_metadata(
    command: AlertActionCommand, *, from_status: AlertStatus, to_status: AlertStatus
) -> dict[str, str]:
    """Build the PHI-free audit metadata for a triage action (ids/enums only — never the note)."""
    metadata = {
        "action": command.action.value,
        "fromStatus": from_status.value,
        "toStatus": to_status.value,
    }
    if command.action == AlertActionType.ASSIGN and command.assignee_id is not None:
        metadata["assigneeId"] = str(command.assignee_id)
    if command.action == AlertActionType.RESOLVE and command.label is not None:
        metadata["label"] = command.label.value
    return metadata

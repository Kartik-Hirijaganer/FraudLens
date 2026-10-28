"""Summary: Contextual maker-checker policy shared by commands and UI capabilities.

Key classes:
- CasePolicy: typed facts from authoritative tenant records.

Key functions:
- action_capabilities: explain every available or blocked case action.
- enforce_action: apply the same policy inside a locked transaction.
- machine_may_enrich: preserve human workflow states on delayed pipeline writes.

Notes:
- Roles cannot override person-based independence. Commenting never establishes preparation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from fraudlens_backend.db.models.enums import AlertStatus
from fraudlens_backend.models.case_workflow import ActionCapability
from fraudlens_backend.models.errors import AppError


class CasePolicy(BaseModel):
    """Authoritative contextual facts, never supplied by a client."""

    mapped: bool = Field(..., description="Acting account has a trusted person link.")
    role: str = Field(..., description="Persisted acting role.")
    status: AlertStatus = Field(..., description="Current case status.")
    unassigned: bool = Field(..., description="No maker owns this case.")
    owner: bool = Field(..., description="Actor is the owning person.")
    preparer: bool = Field(..., description="Actor contributed to the frozen submission.")
    active_submission: bool = Field(..., description="Undecided active submitted round exists.")
    stable: bool = Field(
        ..., description="Machine evidence is persisted and no generation is writing."
    )
    flagged: bool = Field(..., description="Any current authoritative mandatory review flags.")
    usable_sar: bool = Field(..., description="A usable selected SAR exists.")


def action_capabilities(facts: CasePolicy) -> dict[str, ActionCapability]:
    """Derive safe, comprehensible reasons for each command from the same facts."""
    closed = facts.status in {AlertStatus.RESOLVED, AlertStatus.DISMISSED}
    common = (
        "Trusted person mapping is required."
        if not facts.mapped
        else "This case is closed."
        if closed
        else "Read-only role."
        if facts.role == "auditor"
        else None
    )
    preparation = (
        common
        or ("Only the owning person can prepare this case." if not facts.owner else None)
        or ("The submitted draft is locked." if facts.status != AlertStatus.IN_REVIEW else None)
    )
    checker = (
        common
        or (
            "Reviewer or Admin permission is required."
            if facts.role not in {"reviewer", "admin"}
            else None
        )
        or (
            "No active submission awaits review."
            if not facts.active_submission or facts.status != AlertStatus.ESCALATED
            else None
        )
        or (
            "Preparation participation prevents an independent decision, including linked accounts."
            if facts.preparer
            else None
        )
    )
    reasons = {
        "pickup": common
        or (
            "This case is already owned or submitted."
            if not facts.unassigned
            or facts.status not in {AlertStatus.OPEN, AlertStatus.PENDING_REVIEW}
            else None
        ),
        "edit": preparation,
        "regenerate": preparation
        or (None if facts.stable else "Machine investigation is still writing evidence."),
        "submit": preparation
        or (None if facts.stable else "Machine investigation is still writing evidence."),
        "dismiss": preparation
        or ("Current review flags require an independent checker." if facts.flagged else None)
        or (None if facts.stable else "Machine investigation is still writing evidence."),
        "comment": common,
        "approve": checker
        or (None if facts.usable_sar else "Approval requires a reviewable submitted SAR."),
        "return": checker,
        "decline": checker,
    }
    return {
        key: ActionCapability(allowed=reason is None, reason=reason)
        for key, reason in reasons.items()
    }


def enforce_action(facts: CasePolicy, action: str) -> None:
    """Deny before writes; preserve every domain row on rejected requests."""
    capability = action_capabilities(facts)[action]
    if not capability.allowed:
        raise AppError(
            "workflow_action_blocked",
            details=[{"field": "action", "message": capability.reason or "Action unavailable."}],
        )


def machine_may_enrich(status: AlertStatus) -> bool:
    """Machine progress cannot rewind owned, submitted or terminal cases."""
    return status in {AlertStatus.OPEN, AlertStatus.PENDING_REVIEW}

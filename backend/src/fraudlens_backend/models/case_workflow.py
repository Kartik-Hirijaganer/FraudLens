"""Summary: Bounded camelCase commands, capability explanations and frozen submission views.

Key classes:
- CaseVersionRequest: optimistic case version fence.
- WorkingSarRequest: versioned manual narrative command.
- SubmissionRequest: rationale and selected SAR/no-SAR recommendation.
- DecisionRequest: checker command; no inline narrative editing.
- WorkflowCommandResult: durable idempotent command response.
- ActionCapability: backend-owned availability and blocking reason.
- SubmissionView: complete frozen round, decision actors and artifact readiness.

Key functions:
- (none)

Notes:
- Unknown fields fail validation; free text is masked once in the domain service.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from fraudlens_backend.db.models.enums import TrainingLabelType
from fraudlens_backend.models.common import CamelModel
from fraudlens_backend.models.sar import SarDraftView


class CaseVersionRequest(CamelModel):
    """Expected case revision shared by every versioned mutation."""

    expected_case_version: int = Field(..., ge=1, description="Case version displayed to caller.")


class WorkingSarRequest(CaseVersionRequest):
    """Save a masked narrative as a fresh working version, including first manual authoring."""

    expected_sar_version: int | None = Field(
        default=None, ge=1, description="Latest SAR version; null only when none exists."
    )
    content: str = Field(
        ..., min_length=1, max_length=20_000, description="Working narrative to mask and version."
    )

    @field_validator("content")
    @classmethod
    def nonblank(cls, value: str) -> str:
        """Whitespace is never a usable narrative."""
        if not value.strip():
            raise ValueError("A narrative is required")
        return value


class SubmissionRequest(CaseVersionRequest):
    """Submit one immutable round of the maker's recommendation."""

    rationale: str = Field(
        ..., min_length=1, max_length=2_000, description="Required masked submission rationale."
    )
    recommendation: Literal["sar", "no_sar"] = Field(
        ..., description="Submit a SAR or recommend no SAR."
    )
    sar_version: int | None = Field(
        default=None, ge=1, description="Exact working version selected for a SAR recommendation."
    )

    @field_validator("rationale")
    @classmethod
    def nonblank(cls, value: str) -> str:
        """Require meaningful rationale."""
        if not value.strip():
            raise ValueError("A rationale is required")
        return value

    @model_validator(mode="after")
    def selected_version(self) -> SubmissionRequest:
        """A SAR recommendation names its exact version; no-SAR carries no version."""
        if (self.recommendation == "sar") != (self.sar_version is not None):
            raise ValueError("Select a SAR version only for a SAR recommendation")
        return self


class DecisionRequest(CaseVersionRequest):
    """An independent checker decides without changing the frozen narrative."""

    decision: Literal["approve", "return", "decline"] = Field(..., description="Checker action.")
    reason: str = Field(
        ...,
        min_length=1,
        max_length=2_000,
        description="Required masked decision reason or changes.",
    )
    outcome: TrainingLabelType | None = Field(
        default=None, description="Compatible outcome for approve/decline; absent for return."
    )

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value: str) -> str:
        """Reject whitespace reasons before any mutation."""
        if not value.strip():
            raise ValueError("A decision reason is required")
        return value

    @model_validator(mode="after")
    def compatible_outcome(self) -> DecisionRequest:
        """Terminal outcomes must agree with the checker decision."""
        allowed: dict[str, set[TrainingLabelType | None]] = {
            "approve": {TrainingLabelType.CONFIRMED_FRAUD, TrainingLabelType.FALSE_NEGATIVE},
            "decline": {TrainingLabelType.BENIGN, TrainingLabelType.FALSE_POSITIVE},
            "return": {None},
        }
        if self.outcome not in allowed[self.decision]:
            raise ValueError("Select an outcome compatible with the decision")
        return self


class WorkflowCommandResult(CamelModel):
    """Serialized in the receipt so an identical retry returns the original result."""

    alert_id: str = Field(..., description="Case id.")
    submission_id: str = Field(..., description="Submission round id.")
    case_version: int = Field(..., description="Resulting case version.")
    status: str = Field(..., description="Resulting case state.")
    decision: str | None = Field(default=None, description="Recorded checker decision, if any.")


class ActionCapability(CamelModel):
    """A server-owned action explanation; mutations always recheck it under lock."""

    allowed: bool = Field(..., description="Whether the current actor may take the action.")
    reason: str | None = Field(default=None, description="Stable safe blocking explanation.")


class SubmissionView(CamelModel):
    """Frozen maker snapshot and its separately recorded decision."""

    submission_id: str = Field(..., description="Round id.")
    round: int = Field(..., description="Monotonic case submission round.")
    maker_account_id: str = Field(..., description="Original owning account.")
    submitted_by_account_id: str | None = Field(
        default=None, description="Actual submitting account from the immutable action reference."
    )
    can_retry_pdf: bool = Field(
        default=False, description="Backend-authorized artifact recovery for this actor."
    )
    maker_person_id: str = Field(..., description="Canonical maker identity.")
    contributor_person_ids: list[str] = Field(..., description="Frozen preparation participants.")
    recommendation: str = Field(..., description="SAR or no-SAR recommendation.")
    rationale: str | None = Field(
        ..., description="Masked action note, nullable for historical readers."
    )
    sar_draft: SarDraftView | None = Field(..., description="Exact frozen SAR version.")
    evidence: dict[str, object] = Field(
        ..., description="Frozen investigation evidence and identifiers."
    )
    decision: str | None = Field(..., description="Recorded checker decision.")
    reason: str | None = Field(..., description="Masked decision action note.")
    outcome: str | None = Field(..., description="Final training outcome.")
    checker_account_id: str | None = Field(..., description="Checker account.")
    checker_person_id: str | None = Field(..., description="Canonical checker identity.")
    pdf_status: str = Field(..., description="unavailable, pending, ready or failed.")
    created_at: datetime = Field(..., description="Submission timestamp.")

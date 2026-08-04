"""Unit tests for the Phase 9 alert/review domain helpers (plan §5.4, §8.5, §10.4): the centralized
alert state machine (`next_alert_status`), the review-flag computation (`compute_review_flags`),
the SAR review-decision legality (`sar_decision_allowed`), and the zero-dependency SAR PDF renderer.
All are pure functions, so they are exercised here without any IO."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from tenancy import new_agency_id

from fraudlens_backend.db.models.enums import AlertActionType, AlertStatus, SarStatus
from fraudlens_backend.db.repositories.alerts import compute_review_flags, next_alert_status
from fraudlens_backend.models.alerts import (
    AlertActionRequest,
    SarReviewDecision,
    SarReviewRequest,
)
from fraudlens_backend.sar.pdf import render_sar_pdf, sar_pdf_key
from fraudlens_backend.services.alert_workflow import AlertActionCommand, sar_decision_allowed
from fraudlens_core import RiskBand


@pytest.mark.parametrize(
    ("current", "action", "expected"),
    [
        (AlertStatus.OPEN, AlertActionType.ASSIGN, AlertStatus.IN_REVIEW),
        (AlertStatus.OPEN, AlertActionType.ESCALATE, AlertStatus.ESCALATED),
        (AlertStatus.OPEN, AlertActionType.COMMENT, AlertStatus.OPEN),
        (AlertStatus.PENDING_REVIEW, AlertActionType.ASSIGN, AlertStatus.IN_REVIEW),
        (AlertStatus.PENDING_REVIEW, AlertActionType.ESCALATE, AlertStatus.ESCALATED),
        (AlertStatus.PENDING_REVIEW, AlertActionType.COMMENT, AlertStatus.PENDING_REVIEW),
        (AlertStatus.IN_REVIEW, AlertActionType.COMMENT, AlertStatus.IN_REVIEW),
        (AlertStatus.IN_REVIEW, AlertActionType.ESCALATE, AlertStatus.ESCALATED),
        (AlertStatus.ESCALATED, AlertActionType.COMMENT, AlertStatus.ESCALATED),
        (AlertStatus.ESCALATED, AlertActionType.ASSIGN, AlertStatus.IN_REVIEW),
        (AlertStatus.OPEN, AlertActionType.RESOLVE, AlertStatus.RESOLVED),
        (AlertStatus.PENDING_REVIEW, AlertActionType.RESOLVE, AlertStatus.RESOLVED),
        (AlertStatus.IN_REVIEW, AlertActionType.RESOLVE, AlertStatus.RESOLVED),
        (AlertStatus.ESCALATED, AlertActionType.RESOLVE, AlertStatus.RESOLVED),
        (AlertStatus.OPEN, AlertActionType.DISMISS, AlertStatus.DISMISSED),
        (AlertStatus.PENDING_REVIEW, AlertActionType.DISMISS, AlertStatus.DISMISSED),
        (AlertStatus.ESCALATED, AlertActionType.DISMISS, AlertStatus.DISMISSED),
        # Terminal alerts admit no further action (illegal -> None).
        (AlertStatus.RESOLVED, AlertActionType.ASSIGN, None),
        (AlertStatus.RESOLVED, AlertActionType.COMMENT, None),
        (AlertStatus.DISMISSED, AlertActionType.RESOLVE, None),
    ],
)
def test_next_alert_status(
    current: AlertStatus, action: AlertActionType, expected: AlertStatus | None
) -> None:
    assert next_alert_status(current, action) == expected


def _flags(result: list[dict[str, str]]) -> set[str]:
    """Extract the flag keys from a compute_review_flags result."""
    return {item["flag"] for item in result}


def test_review_flags_critical_band_only() -> None:
    flags = compute_review_flags(
        risk_band=RiskBand.CRITICAL,
        fraud_probability=0.99,
        sar_status=SarStatus.DRAFT.value,
        low_confidence_margin=0.1,
    )
    assert _flags(flags) == {"critical_risk_band"}


def test_review_flags_low_confidence_within_margin() -> None:
    flags = compute_review_flags(
        risk_band=RiskBand.HIGH,
        fraud_probability=0.55,
        sar_status=SarStatus.DRAFT.value,
        low_confidence_margin=0.1,
    )
    assert _flags(flags) == {"low_model_confidence"}


def test_review_flags_sar_unavailable_when_failed() -> None:
    flags = compute_review_flags(
        risk_band=RiskBand.HIGH,
        fraud_probability=0.99,
        sar_status=SarStatus.FAILED.value,
        low_confidence_margin=0.1,
    )
    assert _flags(flags) == {"sar_unavailable"}


def test_review_flags_can_stack_all_three() -> None:
    flags = compute_review_flags(
        risk_band=RiskBand.CRITICAL,
        fraud_probability=0.5,
        sar_status=SarStatus.FAILED.value,
        low_confidence_margin=0.1,
    )
    assert _flags(flags) == {"critical_risk_band", "low_model_confidence", "sar_unavailable"}
    assert all("reason" in item for item in flags)  # every flag carries a UI-facing reason


def test_review_flags_empty_for_confident_non_critical() -> None:
    assert (
        compute_review_flags(
            risk_band=RiskBand.LOW,
            fraud_probability=0.99,
            sar_status=SarStatus.DRAFT.value,
            low_confidence_margin=0.1,
        )
        == []
    )


def test_review_flags_total_over_partial_run() -> None:
    # A degraded run (no probability, no SAR) still yields a well-formed flag set.
    assert (
        compute_review_flags(
            risk_band=RiskBand.HIGH,
            fraud_probability=None,
            sar_status=None,
            low_confidence_margin=0.1,
        )
        == []
    )


@pytest.mark.parametrize(
    ("status", "decision", "has_edit", "expected"),
    [
        (SarStatus.DRAFT, SarReviewDecision.APPROVE, False, True),
        (SarStatus.REVIEWED, SarReviewDecision.APPROVE, False, True),
        (SarStatus.FAILED, SarReviewDecision.APPROVE, False, False),  # nothing to approve
        (SarStatus.FAILED, SarReviewDecision.APPROVE, True, True),  # edit supplies content
        (SarStatus.DRAFT, SarReviewDecision.EDIT, False, True),
        (SarStatus.FAILED, SarReviewDecision.EDIT, False, True),
        (SarStatus.DRAFT, SarReviewDecision.REJECT, False, True),
        (SarStatus.APPROVED, SarReviewDecision.APPROVE, True, False),  # terminal
        (SarStatus.APPROVED, SarReviewDecision.EDIT, True, False),
        (SarStatus.REJECTED, SarReviewDecision.REJECT, False, False),
    ],
)
def test_sar_decision_allowed(
    status: SarStatus, decision: SarReviewDecision, has_edit: bool, expected: bool
) -> None:
    assert sar_decision_allowed(status, decision, has_edit=has_edit) == expected


def test_render_sar_pdf_is_valid_minimal_pdf() -> None:
    pdf = render_sar_pdf(
        draft_id="d1", run_id="r1", status="draft", content="line one\nline two", citations=[]
    )
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert b"SUSPICIOUS ACTIVITY REPORT" in pdf


def test_render_sar_pdf_includes_citations() -> None:
    pdf = render_sar_pdf(
        draft_id="d1",
        run_id="r1",
        status="approved",
        content="narrative",
        citations=["31 CFR 1010.314"],
    )
    assert b"Regulatory citations" in pdf
    assert b"31 CFR 1010.314" in pdf


def test_render_sar_pdf_escapes_special_characters() -> None:
    # Parentheses + backslash must be escaped in the PDF literal string (no corruption/crash).
    pdf = render_sar_pdf(
        draft_id="d1", run_id="r1", status="draft", content="amount (USD) \\ wire", citations=[]
    )
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")


def test_render_sar_pdf_truncates_overlong_content() -> None:
    pdf = render_sar_pdf(
        draft_id="d1",
        run_id="r1",
        status="draft",
        content="\n".join(f"line {index}" for index in range(500)),
        citations=[],
    )
    assert pdf.startswith(b"%PDF-1.4")
    assert b"truncated" in pdf


def test_render_sar_pdf_wraps_long_lines_and_blank_lines() -> None:
    # A blank line (preserved) plus a line longer than the wrap width (forces wrapping).
    long_line = "word " * 50
    pdf = render_sar_pdf(
        draft_id="d1", run_id="r1", status="draft", content=f"intro\n\n{long_line}", citations=[]
    )
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")


def test_sar_pdf_key_is_phi_free() -> None:
    agency = new_agency_id()
    draft = uuid.UUID("99999999-9999-4999-8999-999999999999")
    assert sar_pdf_key(agency, draft) == f"sar/{agency}/{draft}.pdf"


def test_action_request_assign_requires_assignee() -> None:
    with pytest.raises(ValidationError):
        AlertActionRequest(action=AlertActionType.ASSIGN)


def test_action_command_assign_requires_assignee() -> None:
    # The domain command carries the same invariant, so a non-HTTP caller (the portfolio-demo
    # bootstrap) cannot reach `record_action` with an unset assignee.
    with pytest.raises(ValidationError):
        AlertActionCommand(
            alert_id=uuid.uuid4(), actor_id=uuid.uuid4(), action=AlertActionType.ASSIGN
        )


def test_action_command_ignores_assignee_on_other_actions() -> None:
    # A comment carrying an assignee stays valid (the pre-extraction routes accepted it).
    command = AlertActionCommand(
        alert_id=uuid.uuid4(),
        actor_id=uuid.uuid4(),
        action=AlertActionType.COMMENT,
        assignee_id=uuid.uuid4(),
    )
    assert command.action is AlertActionType.COMMENT


def test_action_request_resolve_requires_label() -> None:
    with pytest.raises(ValidationError):
        AlertActionRequest(action=AlertActionType.RESOLVE)


def test_sar_review_request_edit_requires_content() -> None:
    with pytest.raises(ValidationError):
        SarReviewRequest(decision=SarReviewDecision.EDIT)


def test_sar_review_request_reject_requires_reason() -> None:
    with pytest.raises(ValidationError):
        SarReviewRequest(decision=SarReviewDecision.REJECT)

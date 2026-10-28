"""Unit proofs for contextual maker-checker policy, current review flags and complete PDFs."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from tenancy import new_agency_id

from fraudlens_backend.db.models.enums import AlertStatus, SarStatus
from fraudlens_backend.db.repositories.alerts import compute_review_flags
from fraudlens_backend.models.alerts import AlertActionRequest
from fraudlens_backend.models.case_workflow import (
    DecisionRequest,
    SubmissionRequest,
    WorkingSarRequest,
)
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.sar.pdf import render_sar_pdf, sar_pdf_key
from fraudlens_backend.services.case_policy import CasePolicy, action_capabilities, enforce_action
from fraudlens_core import RiskBand


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


def test_render_sar_pdf_renders_every_line_across_pages() -> None:
    pdf = render_sar_pdf(
        draft_id="d1",
        run_id="r1",
        status="draft",
        content="\n".join(f"line {index}" for index in range(500)),
        citations=[],
    )
    assert pdf.startswith(b"%PDF-1.4")
    assert b"truncated" not in pdf
    assert b"line 499" in pdf
    assert b"/Count 11" in pdf


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


@pytest.fixture
def facts() -> CasePolicy:
    return CasePolicy(
        mapped=True,
        role="analyst",
        status=AlertStatus.IN_REVIEW,
        unassigned=False,
        owner=True,
        preparer=True,
        active_submission=False,
        stable=True,
        flagged=False,
        usable_sar=True,
    )


@pytest.mark.parametrize("role", ["analyst", "reviewer", "admin"])
def test_all_maker_roles_can_prepare_and_submit(facts: CasePolicy, role: str) -> None:
    caps = action_capabilities(facts.model_copy(update={"role": role}))
    assert all(caps[key].allowed for key in ("edit", "regenerate", "submit", "dismiss", "comment"))
    assert not caps["pickup"].allowed and not caps["approve"].allowed


@pytest.mark.parametrize("action", ["approve", "return", "decline"])
@pytest.mark.parametrize("role", ["reviewer", "admin"])
def test_preparers_cannot_check_in_any_role(facts: CasePolicy, action: str, role: str) -> None:
    submitted = facts.model_copy(
        update={"role": role, "status": AlertStatus.ESCALATED, "active_submission": True}
    )
    with pytest.raises(AppError):
        enforce_action(submitted, action)
    independent = submitted.model_copy(update={"owner": False, "preparer": False})
    assert action_capabilities(independent)[action].allowed


@pytest.mark.parametrize("status", [AlertStatus.RESOLVED, AlertStatus.DISMISSED])
def test_closed_cases_allow_no_mutation(facts: CasePolicy, status: AlertStatus) -> None:
    assert all(
        not cap.allowed
        for cap in action_capabilities(facts.model_copy(update={"status": status})).values()
    )


def test_flags_and_writing_evidence_block_closure_and_submission(facts: CasePolicy) -> None:
    assert not action_capabilities(facts.model_copy(update={"flagged": True}))["dismiss"].allowed
    writing = action_capabilities(facts.model_copy(update={"stable": False}))
    assert all(not writing[key].allowed for key in ("submit", "dismiss", "regenerate"))
    assert writing["edit"].allowed


@pytest.mark.parametrize("changes", [{"mapped": False}, {"role": "auditor"}])
def test_unmapped_and_auditor_fail_closed(facts: CasePolicy, changes: dict) -> None:
    assert all(
        not cap.allowed for cap in action_capabilities(facts.model_copy(update=changes)).values()
    )


@pytest.mark.parametrize("action", ["comment", "dismiss"])
def test_actions_require_nonblank_notes(action: str) -> None:
    with pytest.raises(ValidationError):
        AlertActionRequest(action=action, expected_case_version=1, note=" ")


@pytest.mark.parametrize(
    "decision,outcome",
    [("approve", "benign"), ("decline", "confirmed_fraud"), ("return", "benign")],
)
def test_incompatible_decision_outcomes_fail_validation(decision: str, outcome: str) -> None:
    with pytest.raises(ValidationError):
        DecisionRequest(
            expected_case_version=1, decision=decision, reason="Evidence reviewed", outcome=outcome
        )


def test_versioned_boundaries_reject_blank_and_inline_editing() -> None:
    with pytest.raises(ValidationError):
        WorkingSarRequest(expected_case_version=1, content=" ")
    with pytest.raises(ValidationError):
        SubmissionRequest(expected_case_version=1, recommendation="sar", rationale="Reason")
    with pytest.raises(ValidationError):
        SubmissionRequest(expected_case_version=1, recommendation="no_sar", rationale=" ")
    with pytest.raises(ValidationError):
        DecisionRequest(
            expected_case_version=1, decision="approve", reason=" ", outcome="confirmed_fraud"
        )
    with pytest.raises(ValidationError):
        DecisionRequest(
            expected_case_version=1,
            decision="approve",
            reason="Reviewed",
            outcome="confirmed_fraud",
            edited_content="Bypass",
        )
    assert AlertActionRequest(action="assign", expected_case_version=1).assignee_id is None

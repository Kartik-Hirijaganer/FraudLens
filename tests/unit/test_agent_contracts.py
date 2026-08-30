"""Unit tests for typed multi-agent response and execution contracts."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from fraudlens_backend.agents.config import AgentRole
from fraudlens_backend.agents.contracts import (
    AgentExecutionRecord,
    AgentExecutionStatus,
    AgentToolCallRecord,
    AgentToolCallStatus,
    EvidenceBrief,
    RegulatoryBrief,
    ReviewVerdict,
)


def test_agent_response_contracts_accept_camelcase_and_forbid_extras() -> None:
    evidence = EvidenceBrief.model_validate(
        {
            "summary": "Two deterministic indicators align.",
            "findings": [
                {
                    "statement": "The amount pattern warrants human review.",
                    "evidenceRefs": ["rule-hit:structuring"],
                }
            ],
            "limitations": [],
        }
    )
    regulatory = RegulatoryBrief.model_validate(
        {
            "summary": "One provision may apply.",
            "findings": [
                {
                    "citationId": "31 CFR 1010.314",
                    "title": "Structured transactions",
                    "application": "The supplied pattern matches the reporting concern.",
                }
            ],
        }
    )
    verdict = ReviewVerdict.model_validate(
        {
            "decision": "revise",
            "unsupportedClaimIndexes": [1],
            "fabricatedCitationIds": [],
            "materialityNotes": ["Clarify the activity sequence."],
        }
    )

    assert evidence.model_dump(by_alias=True)["findings"][0]["evidenceRefs"] == (
        "rule-hit:structuring",
    )
    assert regulatory.findings[0].citation_id == "31 CFR 1010.314"
    assert verdict.unsupported_claim_indexes == (1,)
    with pytest.raises(ValidationError):
        EvidenceBrief(summary="safe", unknown="x")  # type: ignore[call-arg]


def test_agent_response_schemas_expose_camelcase_fields() -> None:
    evidence_schema = EvidenceBrief.model_json_schema(by_alias=True)
    regulation_schema = RegulatoryBrief.model_json_schema(by_alias=True)
    verdict_schema = ReviewVerdict.model_json_schema(by_alias=True)

    assert "evidenceRefs" in str(evidence_schema)
    assert "citationId" in str(regulation_schema)
    assert "unsupportedClaimIndexes" in verdict_schema["properties"]


def test_execution_record_round_trips_structured_audit_data() -> None:
    record = AgentExecutionRecord(
        agent=AgentRole.EVIDENCE_INVESTIGATOR,
        attempt=1,
        status=AgentExecutionStatus.DEGRADED,
        error_code="unauthorized_tool_call",
        model_id="openrouter/example/model",
        prompt_version="v1@1.0.0",
        prompt_hash="a" * 64,
        input_hash="b" * 64,
        result_hash="c" * 64,
        latency_ms=12,
        input_tokens=10,
        output_tokens=4,
        total_tokens=14,
        cost_usd=Decimal("0.000123"),
        result={"summary": "safe"},
        tool_calls=(
            AgentToolCallRecord(
                call_id="call-1",
                name="other",
                arguments={},
                status=AgentToolCallStatus.REFUSED,
                error_code="unauthorized_tool_call",
            ),
        ),
        guardrail_decision="flag",
    )

    payload = record.model_dump(mode="json", by_alias=True)
    assert payload["modelId"] == "openrouter/example/model"
    assert payload["toolCalls"][0]["status"] == "refused"
    assert AgentExecutionRecord.model_validate(payload) == record

"""Summary: Typed structured-output and execution contracts for SAR agents.
All provider response boundaries are frozen Pydantic models with camelCase JSON
aliases. Execution records contain only PHI-safe hashes, metrics, structured
results, and stable error codes suitable for later persistence.

Key classes:
- AgentContract: immutable camelCase base for agent boundaries.
- EvidenceFinding: one evidence-backed factual finding.
- EvidenceBrief: evidence investigator structured output.
- RegulatoryFinding: one applicable regulatory provision.
- RegulatoryBrief: regulatory analyst structured output.
- ReviewDecision: bounded pass-or-revise reviewer decision.
- ReviewVerdict: compliance reviewer structured output.
- AgentToolCallStatus: completed, refused, or failed tool outcome.
- AgentToolCallRecord: auditable record of one bounded tool request.
- AgentExecutionRecord: normalized outcome and telemetry for one agent attempt.

Key functions:
- agent_run_id: derive the stable UI/SSE id for a persisted run/role/attempt.

Notes:
- Free-form provider exceptions are never stored in these records.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic.alias_generators import to_camel

from fraudlens_backend.agents.config import AgentRole
from fraudlens_backend.db.models.enums import AgentExecutionStatus as _AgentExecutionStatus
from fraudlens_llm import GuardrailDecision

AgentExecutionStatus = _AgentExecutionStatus


class AgentContract(BaseModel):
    """Immutable camelCase base for provider-facing agent contracts."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class EvidenceFinding(AgentContract):
    """One concise factual finding linked to persisted evidence identifiers."""

    statement: str = Field(..., min_length=1, description="PHI-masked factual finding.")
    evidence_refs: tuple[str, ...] = Field(
        ..., min_length=1, description="Persisted identifiers supporting the finding."
    )


class EvidenceBrief(AgentContract):
    """Structured evidence-investigation output consumed by the SAR writer."""

    summary: str = Field(..., min_length=1, description="Concise PHI-masked evidence summary.")
    findings: tuple[EvidenceFinding, ...] = Field(
        default=(), description="Evidence-backed findings available to the writer."
    )
    limitations: tuple[str, ...] = Field(
        default=(), description="Known evidence gaps requiring human awareness."
    )


class RegulatoryFinding(AgentContract):
    """One regulatory provision and its evidence-bounded applicability."""

    citation_id: str = Field(..., min_length=1, description="Exact persisted citation identifier.")
    title: str = Field(..., min_length=1, description="Provision title from the corpus.")
    application: str = Field(
        ..., min_length=1, description="Why the provision may apply to the supplied evidence."
    )


class RegulatoryBrief(AgentContract):
    """Structured regulatory-analysis output consumed by the SAR writer."""

    summary: str = Field(..., min_length=1, description="Concise regulatory analysis summary.")
    findings: tuple[RegulatoryFinding, ...] = Field(
        default=(), description="Applicable persisted regulatory findings."
    )
    limitations: tuple[str, ...] = Field(
        default=(), description="Known regulatory gaps or ambiguities."
    )


class ReviewDecision(StrEnum):
    """Bounded reviewer outcomes; neither value grants human approval."""

    PASS = "pass"
    REVISE = "revise"


class ReviewVerdict(AgentContract):
    """Structured compliance review of a draft before human decision-making."""

    decision: ReviewDecision = Field(..., description="Whether the draft passes or needs revision.")
    reasons: tuple[str, ...] = Field(
        default=(), description="Concise PHI-masked reasons supporting the decision."
    )
    unsupported_claim_indexes: tuple[int, ...] = Field(
        default=(), description="Zero-based draft claim indexes lacking evidence."
    )
    fabricated_citation_ids: tuple[str, ...] = Field(
        default=(), description="Cited identifiers absent from the supplied corpus evidence."
    )
    materiality_notes: tuple[str, ...] = Field(
        default=(), description="Materiality, tone, or regulatory-fit observations."
    )


class AgentToolCallStatus(StrEnum):
    """Normalized result for one model-requested tool invocation."""

    COMPLETED = "completed"
    REFUSED = "refused"
    FAILED = "failed"


class AgentToolCallRecord(AgentContract):
    """Auditable, structured record of one bounded tool request."""

    call_id: str = Field(..., min_length=1, description="Provider-issued invocation identifier.")
    name: str = Field(..., min_length=1, description="Requested tool name.")
    arguments: dict[str, JsonValue] = Field(
        default_factory=dict, description="Validated PHI-masked tool arguments."
    )
    status: AgentToolCallStatus = Field(..., description="Tool invocation outcome.")
    error_code: str | None = Field(
        default=None, description="Stable PHI-free failure code when not completed."
    )
    result: dict[str, JsonValue] | None = Field(
        default=None, description="Structured PHI-masked tool result when completed."
    )


class AgentExecutionRecord(AgentContract):
    """Normalized structured outcome and telemetry for one agent attempt."""

    agent: AgentRole = Field(..., description="Agent role executed.")
    attempt: int = Field(..., ge=1, description="One-based attempt number for this role.")
    status: AgentExecutionStatus = Field(..., description="Completed, degraded, or failed outcome.")
    error_code: str | None = Field(
        default=None, description="Stable PHI-free outcome code when not fully successful."
    )
    model_id: str = Field(..., min_length=1, description="Catalog reference that served the call.")
    prompt_version: str = Field(..., min_length=1, description="Versioned prompt identifier.")
    prompt_hash: str = Field(..., min_length=1, description="SHA-256 of the exact prompt file.")
    input_hash: str = Field(..., min_length=1, description="SHA-256 of the canonical agent input.")
    result_hash: str | None = Field(
        default=None, description="SHA-256 of the canonical structured result."
    )
    latency_ms: int = Field(..., ge=0, description="Wall-clock duration for the attempt.")
    model_call_count: int = Field(
        default=0,
        ge=0,
        description="Successful provider generations completed during this attempt.",
    )
    input_tokens: int = Field(default=0, ge=0, description="Total provider input tokens.")
    output_tokens: int = Field(default=0, ge=0, description="Total provider output tokens.")
    total_tokens: int = Field(default=0, ge=0, description="Total provider tokens.")
    cost_usd: Decimal = Field(default=Decimal("0"), ge=0, description="Estimated total USD cost.")
    result: dict[str, JsonValue] | None = Field(
        default=None, description="Validated structured output when available."
    )
    tool_calls: tuple[AgentToolCallRecord, ...] = Field(
        default=(), description="Ordered tool invocation audit records."
    )
    guardrail_decision: GuardrailDecision | None = Field(
        default=None, description="Strictest guardrail decision observed across model calls."
    )


def agent_run_id(run_id: uuid.UUID, agent: AgentRole, attempt: int) -> str:
    """Derive the stable UI/SSE identity for one persisted run/role/attempt."""
    return str(uuid.uuid5(run_id, f"{agent.value}:{attempt}"))

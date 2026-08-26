"""Summary: Public Pydantic models and security enums for FraudLens LLM calls.
These are the typed data boundaries shared by the client, adapters, guardrail
pipeline, and callers.

Key classes:
- DataClass: Sensitivity classes used by provider governance policy.
- Strictness: Guardrail enforcement modes.
- PhiMaskingMode: Controls whether local PHI masking is enforced.
- GuardrailDecision: Normalized guardrail decisions.
- TaskType: Call intent used to tune deterministic guardrail handling.
- Role: Chat message role values.
- ToolDefinition: Provider-neutral function tool definition.
- ToolCall: Provider-neutral function tool invocation.
- LlmMessage: Typed chat message boundary.
- Finding: Guardrail finding with category, severity, and location.
- ScanOutcome: Guardrail scan decision plus findings.
- MaskingReport: Counts-only PHI masking report.
- GuardrailReport: Complete guardrail report attached to every result.
- LlmUsage: Normalized token usage.
- LlmResult: Normalized chat generation result.
- EmbeddingResult: Normalized embedding result.

Key functions:
- (none)

Notes:
- raw_text is excluded from dumps and repr by model field configuration.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

_TOOL_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class DataClass(StrEnum):
    """Sensitivity class for compliance-aware provider routing."""

    SYNTHETIC = "synthetic"
    DEIDENTIFIED = "deidentified"
    INTERNAL = "internal"
    RESTRICTED = "restricted"


class Strictness(StrEnum):
    """Guardrail enforcement modes."""

    BLOCK = "block"
    FLAG = "flag"
    DISABLED = "disabled"


class PhiMaskingMode(StrEnum):
    """Local PHI masking modes."""

    ENFORCE = "enforce"
    OFF = "off"


class GuardrailDecision(StrEnum):
    """Normalized outcome for a guardrail stage."""

    ALLOW = "allow"
    FLAG = "flag"
    BLOCK = "block"
    NOT_APPLICABLE = "not_applicable"


class TaskType(StrEnum):
    """High-level task intent for deterministic guardrail tuning."""

    GENERATION = "generation"
    ANALYSIS = "analysis"
    EXTRACTION = "extraction"


class Role(StrEnum):
    """Supported chat message roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolDefinition(BaseModel):
    """Provider-neutral JSON-Schema function tool definition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(
        ...,
        pattern=_TOOL_NAME_PATTERN,
        description="Stable tool name exposed to the model.",
    )
    description: str = Field(
        ...,
        min_length=1,
        description="Plain-language description of the read-only capability.",
    )
    parameters: dict[str, JsonValue] = Field(
        ...,
        description="JSON Schema describing the accepted tool arguments.",
    )


class ToolCall(BaseModel):
    """Provider-neutral function tool invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., min_length=1, description="Provider-issued tool invocation id.")
    name: str = Field(
        ...,
        pattern=_TOOL_NAME_PATTERN,
        description="Declared tool name requested by the model.",
    )
    arguments: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Parsed JSON arguments supplied to the tool.",
    )


class LlmMessage(BaseModel):
    """Typed chat message boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role = Field(..., description="Role of the chat message.")
    content: str | None = Field(
        default=None,
        min_length=1,
        description="Text content, optional only for an assistant tool request.",
    )
    tool_calls: tuple[ToolCall, ...] = Field(
        default=(),
        description="Tool invocations emitted by an assistant message.",
    )
    tool_call_id: str | None = Field(
        default=None,
        min_length=1,
        description="Invocation id answered by a tool-role result message.",
    )

    @model_validator(mode="after")
    def validate_role_payload(self) -> Self:
        """Require role-appropriate text, tool calls, and tool result ids."""
        if self.tool_calls and self.role != Role.ASSISTANT:
            raise ValueError("tool_calls are valid only on assistant messages")
        if self.tool_call_id is not None and self.role != Role.TOOL:
            raise ValueError("tool_call_id is valid only on tool messages")
        if self.role == Role.TOOL and self.tool_call_id is None:
            raise ValueError("tool messages require tool_call_id")
        if self.content is None and not (self.role == Role.ASSISTANT and self.tool_calls):
            raise ValueError("message content is required unless assistant tool_calls are present")
        return self


class Finding(BaseModel):
    """Single deterministic guardrail finding."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: str = Field(..., min_length=1, description="Guardrail category.")
    severity: Literal["low", "medium", "high", "critical"] = Field(
        ..., description="Finding severity."
    )
    location: str = Field(..., min_length=1, description="Coarse finding location.")


class ScanOutcome(BaseModel):
    """Guardrail decision and counts-only finding details."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: GuardrailDecision = Field(..., description="Guardrail decision.")
    findings: list[Finding] = Field(
        default_factory=list, description="Non-sensitive finding metadata."
    )


class MaskingReport(BaseModel):
    """Counts-only local PHI masking report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: PhiMaskingMode = Field(..., description="PHI masking mode used.")
    counts: dict[str, int] = Field(
        default_factory=dict, description="Masked counts keyed by PHI category."
    )
    total_masked: int = Field(..., ge=0, description="Total number of masked spans.")


class GuardrailReport(BaseModel):
    """Complete guardrail report attached to each public result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: GuardrailDecision = Field(..., description="Overall guardrail decision.")
    strictness: Strictness = Field(..., description="Guardrail strictness setting.")
    masking: MaskingReport = Field(..., description="PHI masking report.")
    prompt_risk: ScanOutcome = Field(..., description="Prompt-risk scan outcome.")
    output: ScanOutcome = Field(..., description="Output policy scan outcome.")
    phishing: ScanOutcome = Field(..., description="Phishing scan outcome.")
    policy: ScanOutcome = Field(..., description="Compliance policy decision.")


class LlmUsage(BaseModel):
    """Normalized token usage returned by adapters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(default=0, ge=0, description="Input token count.")
    output_tokens: int = Field(default=0, ge=0, description="Output token count.")
    total_tokens: int = Field(default=0, ge=0, description="Total token count.")


class LlmResult(BaseModel):
    """Normalized chat generation result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    safe_text: str = Field(..., description="Sanitized text safe to return to callers.")
    model: str = Field(..., description="Requested provider/model reference.")
    provider: str = Field(..., description="Resolved provider name.")
    served_model: str | None = Field(default=None, description="Provider-reported served model.")
    finish_reason: str | None = Field(default=None, description="Provider finish reason.")
    usage: LlmUsage = Field(..., description="Normalized usage metrics.")
    tool_calls: tuple[ToolCall, ...] = Field(
        default=(),
        description="Validated tool invocations requested by the model.",
    )
    guardrail: GuardrailReport = Field(..., description="Guardrail report for the call.")
    raw_text: str | None = Field(
        default=None,
        exclude=True,
        repr=False,
        description="Raw provider output, excluded from serialization and repr.",
    )


class EmbeddingResult(BaseModel):
    """Normalized embedding result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    embeddings: list[list[float]] = Field(..., description="Embedding vectors.")
    model: str = Field(..., description="Requested provider/model reference.")
    provider: str = Field(..., description="Resolved provider name.")
    usage: LlmUsage = Field(..., description="Normalized usage metrics.")
    guardrail: GuardrailReport = Field(..., description="Guardrail report for the call.")

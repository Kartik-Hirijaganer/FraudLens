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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class LlmMessage(BaseModel):
    """Typed chat message boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role = Field(..., description="Role of the chat message.")
    content: str = Field(..., min_length=1, description="Text content for the message.")


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

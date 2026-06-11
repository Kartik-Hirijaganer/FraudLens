"""Summary: Safe logging allowlist for LLM calls. It exposes only operational
metadata such as request id, provider, model reference, data class, latency, status,
usage, cost estimate, retry/fallback counts, and guardrail decisions.

Key classes:
- SafeLogEvent: Pydantic allowlist for LLM log metadata.

Key functions:
- safe_log_event: Build a safe log payload from allowed metadata.
- scrub_exception: Return a non-sensitive exception class label.

Notes:
- Prompts, completions, headers, API keys, raw payloads, and tenant ids are not fields.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_llm.models import DataClass, GuardrailDecision, LlmUsage


class SafeLogEvent(BaseModel):
    """Allowlisted LLM operational log metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str | None = Field(default=None, description="Optional request correlation id.")
    model: str = Field(..., description="Requested provider/model reference.")
    provider: str = Field(..., description="Resolved provider name.")
    data_class: DataClass = Field(..., description="Call data class.")
    latency_ms: int | None = Field(default=None, ge=0, description="Provider latency in ms.")
    status: str = Field(..., description="Coarse status label.")
    input_tokens: int = Field(default=0, ge=0, description="Input token count.")
    output_tokens: int = Field(default=0, ge=0, description="Output token count.")
    total_tokens: int = Field(default=0, ge=0, description="Total token count.")
    estimated_cost_usd: float | None = Field(
        default=None, ge=0, description="Estimated cost from verified catalog pricing."
    )
    retry_count: int = Field(default=0, ge=0, description="Retry count observed by caller.")
    fallback_count: int = Field(default=0, ge=0, description="Fallback attempts used.")
    guardrail_decision: GuardrailDecision = Field(..., description="Overall guardrail decision.")
    policy_decision: GuardrailDecision = Field(..., description="Provider policy decision.")


def safe_log_event(  # noqa: PLR0913 - safe log payload is an explicit allowlist.
    *,
    model: str,
    provider: str,
    data_class: DataClass,
    status: str,
    usage: LlmUsage,
    guardrail_decision: GuardrailDecision,
    policy_decision: GuardrailDecision,
    latency_ms: int | None = None,
    estimated_cost_usd: float | None = None,
    retry_count: int = 0,
    fallback_count: int = 0,
    request_id: str | None = None,
) -> dict[str, object]:
    """Build a safe log payload from allowlisted metadata."""
    event = SafeLogEvent(
        request_id=request_id,
        model=model,
        provider=provider,
        data_class=data_class,
        latency_ms=latency_ms,
        status=status,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        estimated_cost_usd=estimated_cost_usd,
        retry_count=retry_count,
        fallback_count=fallback_count,
        guardrail_decision=guardrail_decision,
        policy_decision=policy_decision,
    )
    return event.model_dump()


def scrub_exception(exc: BaseException) -> str:
    """Return a non-sensitive exception class label."""
    return exc.__class__.__name__

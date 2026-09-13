"""Summary: Attempt-local accounting contracts for the bounded SAR agent runtime.

Key classes:
- AgentBudgetExceededError: pre-call worst-case budget refusal.
- ExecutionState: mutable accounting retained across timeout cancellation.

Key functions:
- decision_rank:

Notes:
- ExecutionState stores only structured counters, safe codes, and guardrail decisions.
"""

from decimal import Decimal

from fraudlens_backend.agents.contracts import AgentToolCallRecord
from fraudlens_llm import GuardrailDecision, LlmResult


class AgentBudgetExceededError(RuntimeError):
    """Raised before provider access when configured worst-case cost exceeds the cap."""


class ExecutionState:
    """Mutable attempt-local accounting retained across timeout cancellation."""

    def __init__(self, *, model_id: str) -> None:
        """Initialize empty, PHI-free execution accounting."""
        self.model_id = model_id
        self.model_call_count = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.cost_usd = Decimal("0")
        self.tool_calls: list[AgentToolCallRecord] = []
        self.tool_call_count = 0
        self.guardrail_decision: GuardrailDecision | None = None
        self.degraded_code: str | None = None

    def record_result(self, result: LlmResult, *, cost_usd: Decimal) -> None:
        """Accumulate one completed provider call's usage, cost, and guardrails."""
        self.model_id = result.model
        self.model_call_count += 1
        self.input_tokens += result.usage.input_tokens
        self.output_tokens += result.usage.output_tokens
        self.total_tokens += result.usage.total_tokens
        self.cost_usd += cost_usd
        if decision_rank(result.guardrail.decision) > decision_rank(self.guardrail_decision):
            self.guardrail_decision = result.guardrail.decision

    def mark_degraded(self, error_code: str) -> None:
        """Retain the first degraded-path code for stable downstream interpretation."""
        if self.degraded_code is None:
            self.degraded_code = error_code


def decision_rank(decision: GuardrailDecision | None) -> int:
    """Rank guardrail outcomes for strictest-decision aggregation."""
    ranks = {
        None: 0,
        GuardrailDecision.NOT_APPLICABLE: 0,
        GuardrailDecision.ALLOW: 1,
        GuardrailDecision.FLAG: 2,
        GuardrailDecision.BLOCK: 3,
    }
    return ranks[decision]

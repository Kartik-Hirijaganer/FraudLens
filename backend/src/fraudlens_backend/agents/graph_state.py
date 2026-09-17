"""Summary: Typed contracts and state boundary for the bounded SAR agent graph.

Key classes:
- AgentExecutor: structural execution seam implemented by AgentRuntime.
- AgentReviewStatus: terminal and routing outcomes.
- AgentGraphResult: typed terminal graph result.
- AgentGraphState: in-memory LangGraph state.

Key functions:
- (none)

Notes:
- Parallel investigator nodes write disjoint keys in AgentGraphState.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Protocol, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_backend.agents.checks import DeterministicReviewChecks
from fraudlens_backend.agents.config import AgentRole
from fraudlens_backend.agents.contracts import (
    AgentExecutionRecord,
    EvidenceBrief,
    RegulatoryBrief,
    ReviewVerdict,
)
from fraudlens_backend.agents.prompts import AgentPromptTemplate
from fraudlens_backend.agents.resume import CompletedAgentExecutions
from fraudlens_backend.sar.egress import SarModelInput
from fraudlens_ml.sar import SarDraftContent, SarInput, SarStreamEvent

AgentEventEmitter = Callable[[SarStreamEvent], Awaitable[None]]
AgentExecutionRecorder = Callable[[AgentExecutionRecord], Awaitable[None]]
REVIEW_UNAVAILABLE_CODES = frozenset(
    {"agent_timeout", "llm_retryable_error", "llm_non_retryable_error", "agent_runtime_error"}
)


class AgentExecutor(Protocol):
    """Structural execution seam implemented by AgentRuntime."""

    async def execute(
        self,
        *,
        agent: AgentRole,
        prompt: AgentPromptTemplate,
        user_content: str,
        response_model: type[BaseModel],
        attempt: int = 1,
    ) -> AgentExecutionRecord:
        """Execute one bounded role attempt."""


class AgentReviewStatus(StrEnum):
    """Stable review-routing outcomes; none represents human approval."""

    PASSED = "review_passed"
    REVISION_REQUESTED = "revision_requested"
    UNRESOLVED = "review_unresolved"
    UNAVAILABLE = "review_unavailable"
    WRITER_FAILED = "writer_failed"


class AgentGraphResult(BaseModel):
    """Typed workflow result retained for terminal drafting and persistence."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    content: SarDraftContent | None = Field(
        default=None, description="Latest ungrounded writer output, or null when writing failed."
    )
    checks: DeterministicReviewChecks | None = Field(
        default=None, description="Deterministic checks for the latest writer output."
    )
    review_status: AgentReviewStatus = Field(..., description="Terminal review workflow status.")
    revision_count: int = Field(..., ge=0, description="Number of writer revisions completed.")
    executions: tuple[AgentExecutionRecord, ...] = Field(
        default=(), description="All agent attempts in stable workflow order."
    )
    available_evidence_refs: frozenset[str] = Field(
        default=frozenset(),
        description="Trusted evidence ids harvested from completed agent tool results.",
    )


class AgentGraphState(TypedDict, total=False):
    """In-memory graph state; parallel nodes write disjoint keys by construction."""

    sar_input: SarInput
    model_input: SarModelInput
    emit: AgentEventEmitter
    evidence_brief: EvidenceBrief
    regulatory_brief: RegulatoryBrief
    evidence_execution: AgentExecutionRecord
    regulatory_execution: AgentExecutionRecord
    writer_executions: tuple[AgentExecutionRecord, ...]
    reviewer_executions: tuple[AgentExecutionRecord, ...]
    content: SarDraftContent | None
    checks: DeterministicReviewChecks | None
    review_status: AgentReviewStatus
    revision_count: int
    reviewer_feedback: ReviewVerdict | None
    next_writer_run_id: str | None
    completed_executions: CompletedAgentExecutions

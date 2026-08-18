"""Summary: Structurally bounded LangGraph workflow for four-agent SAR drafting.
Two read-only investigators run in parallel, the writer produces an ungrounded
draft, deterministic checks precede compliance review, and one revision is
permitted only through the graph router.

Key classes:
- AgentExecutor: structural execution seam implemented by AgentRuntime.
- AgentReviewStatus: terminal or routing review outcomes.
- AgentGraphResult: typed in-memory workflow result for the drafter and later persistence.
- AgentGraph: compiled, dependency-injected agent workflow.

Key functions:
- build_agent_graph: wire the parallel investigation and bounded review graph.

Notes:
- No checkpointer is used; completed attempts replay from tenant-scoped persistence by input hash.
- Draft citations are intentionally left ungrounded until the reviewer has seen them.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum
from typing import Any, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fraudlens_backend.agents.checks import DeterministicReviewChecks, evaluate_draft_checks
from fraudlens_backend.agents.config import AgentRole, AgentsConfig
from fraudlens_backend.agents.contracts import (
    AgentExecutionRecord,
    AgentExecutionStatus,
    AgentToolCallStatus,
    EvidenceBrief,
    RegulatoryBrief,
    ReviewDecision,
    ReviewVerdict,
)
from fraudlens_backend.agents.contracts import (
    agent_run_id as stable_agent_run_id,
)
from fraudlens_backend.agents.prompts import AgentPromptTemplate
from fraudlens_backend.agents.resume import (
    AgentExecutionReplayPort,
    CompletedAgentExecutions,
    execution_replay_context,
)
from fraudlens_backend.agents.runtime import agent_input_hash
from fraudlens_ml.sar import (
    SarAgentEvent,
    SarDraftContent,
    SarEventType,
    SarInput,
    SarStreamEvent,
)

AgentEventEmitter = Callable[[SarStreamEvent], Awaitable[None]]
AgentExecutionRecorder = Callable[[AgentExecutionRecord], Awaitable[None]]
_REVIEW_UNAVAILABLE_CODES = frozenset(
    {
        "agent_timeout",
        "llm_retryable_error",
        "llm_non_retryable_error",
        "agent_runtime_error",
    }
)


class AgentExecutor(Protocol):
    """Structural execution seam implemented by the Phase 2 `AgentRuntime`."""

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
    """Typed workflow result retained for terminal drafting and Phase 5 persistence."""

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


class _AgentGraphState(TypedDict, total=False):
    """In-memory graph state; parallel nodes write disjoint keys by construction."""

    sar_input: SarInput
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


class AgentGraph:
    """Compiled bounded agent graph with a typed invocation boundary."""

    def __init__(
        self,
        compiled: Any,
        *,
        replay: AgentExecutionReplayPort | None = None,
    ) -> None:
        """Bind a compiled LangGraph runnable and optional locked replay coordinator."""
        self._compiled = compiled
        self._replay = replay

    async def run(self, sar_input: SarInput, *, emit: AgentEventEmitter) -> AgentGraphResult:
        """Run one in-memory workflow and return its complete typed outcome."""
        async with execution_replay_context(self._replay) as completed:
            raw = cast(
                _AgentGraphState,
                await self._compiled.ainvoke(
                    {
                        "sar_input": sar_input,
                        "emit": emit,
                        "writer_executions": (),
                        "reviewer_executions": (),
                        "content": None,
                        "checks": None,
                        "revision_count": 0,
                        "reviewer_feedback": None,
                        "next_writer_run_id": None,
                        "completed_executions": dict(completed),
                    }
                ),
            )
        executions = (
            raw["evidence_execution"],
            raw["regulatory_execution"],
            *raw.get("writer_executions", ()),
            *raw.get("reviewer_executions", ()),
        )
        return AgentGraphResult(
            content=raw.get("content"),
            checks=raw.get("checks"),
            review_status=raw["review_status"],
            revision_count=raw.get("revision_count", 0),
            executions=executions,
        )


def build_agent_graph(  # noqa: PLR0913, PLR0915 - explicit graph dependencies and nodes.
    *,
    runtime: AgentExecutor,
    config: AgentsConfig,
    prompts: Mapping[AgentRole, AgentPromptTemplate],
    run_id: uuid.UUID | None = None,
    record_execution: AgentExecutionRecorder | None = None,
    replay: AgentExecutionReplayPort | None = None,
) -> AgentGraph:
    """Compile the parallel investigation and structurally capped writer-reviewer graph."""
    missing_prompts = set(AgentRole) - set(prompts)
    if missing_prompts:
        raise ValueError("Agent graph requires a prompt for every role")

    async def execute_role(  # noqa: PLR0913 - lifecycle context is explicit at the call site.
        *,
        state: _AgentGraphState,
        role: AgentRole,
        user_content: str,
        response_model: type[BaseModel],
        attempt: int,
        agent_run_id: str | None = None,
    ) -> AgentExecutionRecord:
        """Emit lifecycle events around one runtime call without leaking prompt content."""
        completed = state.get("completed_executions", {}).get((role, attempt))
        if completed is not None and completed.input_hash == agent_input_hash(
            agent=role,
            prompt=prompts[role],
            user_content=user_content,
        ):
            return completed

        event_run_id = agent_run_id or (
            stable_agent_run_id(run_id, role, attempt) if run_id is not None else str(uuid.uuid4())
        )
        await state["emit"](
            _agent_event(SarEventType.AGENT_STARTED, event_run_id, role, attempt, status="started")
        )
        record = await runtime.execute(
            agent=role,
            prompt=prompts[role],
            user_content=user_content,
            response_model=response_model,
            attempt=attempt,
        )
        if record_execution is not None:
            await record_execution(record)
        for tool_call in record.tool_calls:
            if tool_call.status is AgentToolCallStatus.COMPLETED:
                await state["emit"](
                    _agent_event(
                        SarEventType.AGENT_TOOL_COMPLETED,
                        event_run_id,
                        role,
                        attempt,
                        status=tool_call.status.value,
                        tool_name=tool_call.name,
                    )
                )
        await state["emit"](
            _agent_event(
                SarEventType.AGENT_COMPLETED,
                event_run_id,
                role,
                attempt,
                status=record.status.value,
                error_code=record.error_code,
            )
        )
        return record

    async def node_evidence(state: _AgentGraphState) -> dict[str, Any]:
        """Collect an evidence brief through the run-bound read-only toolset."""
        record = await execute_role(
            state=state,
            role=AgentRole.EVIDENCE_INVESTIGATOR,
            user_content=_base_input_json(state["sar_input"]),
            response_model=EvidenceBrief,
            attempt=1,
        )
        return {
            "evidence_execution": record,
            "evidence_brief": _validated_or_default(
                EvidenceBrief,
                record,
                EvidenceBrief(
                    summary="Evidence analysis was unavailable.",
                    limitations=("Human review must rely on deterministic run evidence.",),
                ),
            ),
        }

    async def node_regulatory(state: _AgentGraphState) -> dict[str, Any]:
        """Collect a bounded regulatory brief through the governed retrieval tool."""
        record = await execute_role(
            state=state,
            role=AgentRole.REGULATORY_ANALYST,
            user_content=_base_input_json(state["sar_input"]),
            response_model=RegulatoryBrief,
            attempt=1,
        )
        return {
            "regulatory_execution": record,
            "regulatory_brief": _validated_or_default(
                RegulatoryBrief,
                record,
                RegulatoryBrief(
                    summary="Regulatory analysis was unavailable.",
                    limitations=("Human review must verify regulatory applicability.",),
                ),
            ),
        }

    async def node_writer(state: _AgentGraphState) -> dict[str, Any]:
        """Create or revise the draft from supplied briefs without grounding citations yet."""
        attempt = len(state.get("writer_executions", ())) + 1
        record = await execute_role(
            state=state,
            role=AgentRole.SAR_WRITER,
            user_content=_writer_input_json(state),
            response_model=SarDraftContent,
            attempt=attempt,
            agent_run_id=state.get("next_writer_run_id"),
        )
        executions = (*state.get("writer_executions", ()), record)
        content = _validated_or_none(SarDraftContent, record)
        if content is None:
            return {
                "writer_executions": executions,
                "content": None,
                "checks": None,
                "review_status": AgentReviewStatus.WRITER_FAILED,
                "next_writer_run_id": None,
            }
        return {
            "writer_executions": executions,
            "content": content,
            "checks": evaluate_draft_checks(
                content,
                state["sar_input"].citations,
                available_evidence_refs=_available_evidence_refs(state, run_id=run_id),
            ),
            "next_writer_run_id": None,
        }

    def route_after_writer(state: _AgentGraphState) -> str:
        """Stop on writer failure; otherwise send the ungrounded draft to review."""
        return END if state.get("content") is None else "reviewer"

    async def node_reviewer(state: _AgentGraphState) -> dict[str, Any]:
        """Review one ungrounded draft and decide pass, one revision, or human-only handling."""
        attempt = len(state.get("reviewer_executions", ())) + 1
        record = await execute_role(
            state=state,
            role=AgentRole.COMPLIANCE_REVIEWER,
            user_content=_reviewer_input_json(state),
            response_model=ReviewVerdict,
            attempt=attempt,
        )
        executions = (*state.get("reviewer_executions", ()), record)
        if _review_is_unavailable(record):
            return {
                "reviewer_executions": executions,
                "review_status": AgentReviewStatus.UNAVAILABLE,
            }

        checks = state["checks"]
        if checks is None:
            raise RuntimeError("Reviewer requires deterministic checks")
        verdict = (
            _validated_or_none(ReviewVerdict, record)
            if record.status is AgentExecutionStatus.COMPLETED
            else None
        )
        needs_revision = not checks.passed or (
            verdict is not None and verdict.decision is ReviewDecision.REVISE
        )
        if not needs_revision:
            return {
                "reviewer_executions": executions,
                "reviewer_feedback": verdict,
                "review_status": AgentReviewStatus.PASSED,
            }

        revision_count = state.get("revision_count", 0)
        if revision_count >= config.workflow.max_revisions:
            return {
                "reviewer_executions": executions,
                "reviewer_feedback": verdict,
                "review_status": AgentReviewStatus.UNRESOLVED,
            }
        next_attempt = len(state.get("writer_executions", ())) + 1
        next_run_id = (
            stable_agent_run_id(run_id, AgentRole.SAR_WRITER, next_attempt)
            if run_id is not None
            else str(uuid.uuid4())
        )
        await state["emit"](
            _agent_event(
                SarEventType.AGENT_REVISION_REQUESTED,
                next_run_id,
                AgentRole.SAR_WRITER,
                next_attempt,
                status=AgentReviewStatus.REVISION_REQUESTED.value,
            )
        )
        return {
            "reviewer_executions": executions,
            "reviewer_feedback": verdict,
            "review_status": AgentReviewStatus.REVISION_REQUESTED,
            "revision_count": revision_count + 1,
            "next_writer_run_id": next_run_id,
        }

    def route_after_reviewer(state: _AgentGraphState) -> str:
        """Use graph state, not model prose, to enforce the revision cap structurally."""
        if (
            state["review_status"] is AgentReviewStatus.REVISION_REQUESTED
            and state["revision_count"] <= config.workflow.max_revisions
        ):
            return "writer"
        return END

    graph = StateGraph(_AgentGraphState)
    graph.add_node("evidence", node_evidence)
    graph.add_node("regulatory", node_regulatory)
    graph.add_node("writer", node_writer)
    graph.add_node("reviewer", node_reviewer)
    graph.add_edge(START, "evidence")
    graph.add_edge(START, "regulatory")
    graph.add_edge(["evidence", "regulatory"], "writer")
    graph.add_conditional_edges("writer", route_after_writer)
    graph.add_conditional_edges("reviewer", route_after_reviewer)
    return AgentGraph(graph.compile(), replay=replay)


def _agent_event(  # noqa: PLR0913 - event identity and optional metadata stay explicit.
    event_type: SarEventType,
    agent_run_id: str,
    role: AgentRole,
    attempt: int,
    *,
    status: str,
    tool_name: str | None = None,
    error_code: str | None = None,
) -> SarStreamEvent:
    """Build one PHI-free lifecycle event."""
    return SarStreamEvent(
        type=event_type,
        agent=SarAgentEvent(
            agent_run_id=agent_run_id,
            agent=role.value,
            attempt=attempt,
            status=status,
            tool_name=tool_name,
            error_code=error_code,
        ),
    )


def _base_input_json(sar_input: SarInput) -> str:
    """Serialize prompt-safe run facts while excluding tenant provenance."""
    payload = sar_input.model_dump(mode="json", by_alias=True, exclude={"agency_id"})
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _writer_input_json(state: _AgentGraphState) -> str:
    """Serialize exact writer inputs, including reviewer feedback for the single revision."""
    reviewer_feedback = state.get("reviewer_feedback")
    deterministic_checks = state.get("checks")
    payload = {
        "runFacts": json.loads(_base_input_json(state["sar_input"])),
        "evidenceBrief": state["evidence_brief"].model_dump(mode="json", by_alias=True),
        "regulatoryBrief": state["regulatory_brief"].model_dump(mode="json", by_alias=True),
        "reviewerFeedback": (
            reviewer_feedback.model_dump(mode="json", by_alias=True)
            if reviewer_feedback is not None
            else None
        ),
        "deterministicChecks": (
            deterministic_checks.model_dump(mode="json", by_alias=True)
            if deterministic_checks is not None
            else None
        ),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _reviewer_input_json(state: _AgentGraphState) -> str:
    """Serialize the ungrounded draft and deterministic checks for compliance review."""
    content = state.get("content")
    checks = state.get("checks")
    if content is None or checks is None:
        raise RuntimeError("Reviewer requires a draft and deterministic checks")
    payload = {
        "draft": content.model_dump(mode="json", by_alias=True),
        "deterministicChecks": checks.model_dump(mode="json", by_alias=True),
        "evidenceBrief": state["evidence_brief"].model_dump(mode="json", by_alias=True),
        "regulatoryBrief": state["regulatory_brief"].model_dump(mode="json", by_alias=True),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _validated_or_none(response_model: type[BaseModel], record: AgentExecutionRecord) -> Any | None:
    """Validate a runtime payload defensively without surfacing provider details."""
    if record.result is None:
        return None
    try:
        return response_model.model_validate(record.result)
    except ValidationError:
        return None


def _validated_or_default(
    response_model: type[BaseModel],
    record: AgentExecutionRecord,
    default: Any,
) -> Any:
    """Return a validated partial-agent payload or a safe empty brief."""
    return _validated_or_none(response_model, record) or default


def _review_is_unavailable(record: AgentExecutionRecord) -> bool:
    """Classify reviewer failures/timeouts separately from usable degraded review."""
    return (
        record.status is AgentExecutionStatus.FAILED
        or record.error_code in _REVIEW_UNAVAILABLE_CODES
    )


def _available_evidence_refs(
    state: _AgentGraphState,
    *,
    run_id: uuid.UUID | None,
) -> frozenset[str]:
    """Collect trusted evidence ids from persisted run facts and completed tool results."""
    sar_input = state["sar_input"]
    refs = {f"transaction:{sar_input.transaction_id}"}
    if run_id is not None:
        refs.update(f"rule-hit:{run_id}:{index}" for index, _hit in enumerate(sar_input.rule_hits))
        refs.update(
            f"shap-driver:{run_id}:{index}" for index, _feature in enumerate(sar_input.top_features)
        )
    executions = (
        state.get("evidence_execution"),
        state.get("regulatory_execution"),
    )
    for execution in executions:
        if execution is None:
            continue
        for tool_call in execution.tool_calls:
            if tool_call.status is AgentToolCallStatus.COMPLETED and tool_call.result is not None:
                _collect_evidence_refs(tool_call.result, refs)
    return frozenset(refs)


def _collect_evidence_refs(value: object, refs: set[str]) -> None:
    """Recursively collect only explicit camelCase evidenceRef fields from typed tool data."""
    if isinstance(value, dict):
        evidence_ref = value.get("evidenceRef")
        if isinstance(evidence_ref, str):
            refs.add(evidence_ref)
        for nested in value.values():
            _collect_evidence_refs(nested, refs)
    elif isinstance(value, list):
        for nested in value:
            _collect_evidence_refs(nested, refs)

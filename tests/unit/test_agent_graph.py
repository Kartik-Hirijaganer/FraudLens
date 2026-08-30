"""Unit tests for the bounded agent graph, deterministic checks, and drafter adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from decimal import Decimal
from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from fraudlens_backend.agents.checks import evaluate_draft_checks
from fraudlens_backend.agents.config import AgentRole, AgentsConfig, load_agents_config
from fraudlens_backend.agents.contracts import (
    AgentExecutionRecord,
    AgentExecutionStatus,
    AgentToolCallRecord,
    AgentToolCallStatus,
    EvidenceBrief,
    EvidenceFinding,
    RegulatoryBrief,
    RegulatoryFinding,
    ReviewDecision,
    ReviewVerdict,
)
from fraudlens_backend.agents.graph import AgentGraph, AgentReviewStatus, build_agent_graph
from fraudlens_backend.agents.prompts import AgentPromptTemplate
from fraudlens_backend.agents.runtime import AgentBudgetExceededError, agent_input_hash
from fraudlens_backend.agents.tools import EvidenceToolset
from fraudlens_backend.sar.budget import BudgetGuard
from fraudlens_backend.sar.drafter_multi_agent import MultiAgentSarDrafter
from fraudlens_backend.sar.factory import build_agent_drafter_factory
from fraudlens_backend.settings import find_config_dir
from fraudlens_llm import Catalog, LlmClient, ToolDefinition, load_catalog
from fraudlens_ml.sar import (
    SarClaim,
    SarDraftContent,
    SarDraftStatus,
    SarEventType,
    SarInput,
    SarStreamEvent,
)

_TOOL_NAMES = {
    "transaction_history",
    "rule_hits",
    "shap_drivers",
    "alert_history",
    "regulation_search",
}
_Outcome = tuple[AgentExecutionStatus, str | None, BaseModel | None]


class _FakeRuntime:
    """Role-queued runtime fake that records exact reviewer and writer inputs."""

    def __init__(self, outcomes: dict[AgentRole, Sequence[_Outcome]]) -> None:
        self.outcomes = {role: list(items) for role, items in outcomes.items()}
        self.inputs: dict[AgentRole, list[str]] = {role: [] for role in AgentRole}

    async def execute(
        self,
        *,
        agent: AgentRole,
        prompt: AgentPromptTemplate,
        user_content: str,
        response_model: type[BaseModel],
        attempt: int = 1,
    ) -> AgentExecutionRecord:
        _ = response_model
        self.inputs[agent].append(user_content)
        status, error_code, result = self.outcomes[agent].pop(0)
        tool_calls = (
            (
                AgentToolCallRecord(
                    call_id="evidence-1",
                    name="rule_hits",
                    status=AgentToolCallStatus.COMPLETED,
                    result={"hits": [{"evidenceRef": "rule-hit:run-1:0"}]},
                ),
            )
            if agent is AgentRole.EVIDENCE_INVESTIGATOR
            else ()
        )
        return AgentExecutionRecord(
            agent=agent,
            attempt=attempt,
            status=status,
            error_code=error_code,
            model_id=_config().agents.for_role(agent).model,
            prompt_version=prompt.prompt_version,
            prompt_hash=prompt.prompt_hash,
            input_hash=agent_input_hash(
                agent=agent,
                prompt=prompt,
                user_content=user_content,
            ),
            result_hash=f"result-{agent.value}-{attempt}" if result is not None else None,
            latency_ms=5,
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
            cost_usd=Decimal("0.001"),
            result=(result.model_dump(mode="json", by_alias=True) if result is not None else None),
            tool_calls=tool_calls,
        )


class _ToolResult(BaseModel):
    """Minimal structured result for the factory's bound tool executor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    available: bool = Field(..., description="Whether the synthetic capability was available.")


class _FakeToolset:
    """Factory-compatible toolset without a database dependency."""

    def __init__(self) -> None:
        schema: dict[str, JsonValue] = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
        self.registry = {name: object() for name in _TOOL_NAMES}
        self.definitions = {
            name: ToolDefinition(
                name=name,
                description="Read governed synthetic evidence.",
                parameters=schema,
            )
            for name in _TOOL_NAMES
        }

    async def execute(self, name: str, arguments: dict[str, JsonValue]) -> BaseModel:
        _ = (name, arguments)
        return _ToolResult(available=True)


class _FaultingGraph:
    """Graph seam that delays or raises for drafter boundary tests."""

    def __init__(self, error: BaseException | None = None, *, delay_s: float = 0) -> None:
        self.error = error
        self.delay_s = delay_s

    async def run(self, sar_input: SarInput, *, emit) -> None:
        _ = (sar_input, emit)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error


def _catalog() -> Catalog:
    return load_catalog(find_config_dir() / "llm" / "catalog.yml")


def _config(**workflow_updates: object) -> AgentsConfig:
    config = load_agents_config(catalog=_catalog(), available_tools=_TOOL_NAMES)
    if not workflow_updates:
        return config
    return config.model_copy(
        update={"workflow": config.workflow.model_copy(update=workflow_updates)}
    )


def _prompts() -> dict[AgentRole, AgentPromptTemplate]:
    config = _config()
    return {
        role: AgentPromptTemplate.load(role, config.agents.for_role(role).prompt_id)
        for role in AgentRole
    }


def _evidence() -> EvidenceBrief:
    return EvidenceBrief(
        summary="The deterministic evidence warrants human review.",
        findings=(
            EvidenceFinding(
                statement="A persisted rule identified a notable pattern.",
                evidence_refs=("rule-hit:run-1:0",),
            ),
        ),
    )


def _regulatory() -> RegulatoryBrief:
    return RegulatoryBrief(
        summary="The supplied provision is relevant to the pattern.",
        findings=(
            RegulatoryFinding(
                citation_id="31 CFR 1010.314",
                title="Structuring",
                application="The persisted pattern may warrant review under this provision.",
            ),
        ),
    )


def _draft(*, supported: bool = True, fabricated: bool = False) -> SarDraftContent:
    citation_id = "99 FAKE 1" if fabricated else "31 CFR 1010.314"
    return SarDraftContent(
        subject="Potential structuring",
        narrative="The persisted transaction pattern warrants human review.",
        claims=(
            SarClaim(
                statement="The persisted rule identified a notable pattern.",
                evidence_refs=("rule-hit:run-1:0",) if supported else (),
                citation_ids=(citation_id,),
            ),
        ),
        cited_regulations=(citation_id,),
        recommended_action="Escalate for human review.",
    )


def _verdict(decision: ReviewDecision) -> ReviewVerdict:
    return ReviewVerdict(decision=decision, reasons=("Bounded synthetic review.",))


def _outcomes(
    *,
    writers: Sequence[_Outcome] | None = None,
    reviewers: Sequence[_Outcome] | None = None,
    evidence: _Outcome | None = None,
) -> dict[AgentRole, Sequence[_Outcome]]:
    completed = AgentExecutionStatus.COMPLETED
    return {
        AgentRole.EVIDENCE_INVESTIGATOR: (evidence or (completed, None, _evidence()),),
        AgentRole.REGULATORY_ANALYST: ((completed, None, _regulatory()),),
        AgentRole.SAR_WRITER: writers or ((completed, None, _draft()),),
        AgentRole.COMPLIANCE_REVIEWER: reviewers
        or ((completed, None, _verdict(ReviewDecision.PASS)),),
    }


async def _run(runtime: _FakeRuntime, sar_input: SarInput, *, config: AgentsConfig | None = None):
    events: list[SarStreamEvent] = []
    graph = build_agent_graph(runtime=runtime, config=config or _config(), prompts=_prompts())

    async def emit(event: SarStreamEvent) -> None:
        events.append(event)

    return await graph.run(sar_input, emit=emit), events


def test_deterministic_checks_report_unsupported_claims_and_fabricated_ids(make_sar_input) -> None:
    checks = evaluate_draft_checks(
        _draft(supported=False, fabricated=True), make_sar_input().citations
    )

    assert checks.passed is False
    assert checks.unsupported_claim_indexes == (0,)
    assert checks.fabricated_citation_ids == ("99 FAKE 1",)


@pytest.mark.asyncio
async def test_pass_first_time_has_four_executions_and_no_revision(make_sar_input) -> None:
    runtime = _FakeRuntime(_outcomes())

    result, events = await _run(runtime, make_sar_input())

    assert result.review_status is AgentReviewStatus.PASSED
    assert result.revision_count == 0
    assert len(result.executions) == 4
    assert [item.type for item in events].count(SarEventType.AGENT_STARTED) == 4
    assert SarEventType.AGENT_REVISION_REQUESTED not in [item.type for item in events]


@pytest.mark.asyncio
async def test_fail_then_pass_runs_one_bounded_revision(make_sar_input) -> None:
    completed = AgentExecutionStatus.COMPLETED
    runtime = _FakeRuntime(
        _outcomes(
            writers=((completed, None, _draft()), (completed, None, _draft())),
            reviewers=(
                (completed, None, _verdict(ReviewDecision.REVISE)),
                (completed, None, _verdict(ReviewDecision.PASS)),
            ),
        )
    )

    result, events = await _run(runtime, make_sar_input())

    assert result.review_status is AgentReviewStatus.PASSED
    assert result.revision_count == 1
    assert len([item for item in result.executions if item.agent is AgentRole.SAR_WRITER]) == 2
    assert [item.type for item in events].count(SarEventType.AGENT_REVISION_REQUESTED) == 1
    revision_input = json.loads(runtime.inputs[AgentRole.SAR_WRITER][1])
    assert revision_input["deterministicChecks"]["passed"] is True
    assert revision_input["reviewerFeedback"]["decision"] == "revise"


@pytest.mark.asyncio
async def test_fail_twice_stops_with_two_writers_and_review_unresolved(make_sar_input) -> None:
    completed = AgentExecutionStatus.COMPLETED
    runtime = _FakeRuntime(
        _outcomes(
            writers=((completed, None, _draft()), (completed, None, _draft())),
            reviewers=(
                (completed, None, _verdict(ReviewDecision.REVISE)),
                (completed, None, _verdict(ReviewDecision.REVISE)),
            ),
        )
    )

    result, _events = await _run(runtime, make_sar_input())

    assert result.review_status is AgentReviewStatus.UNRESOLVED
    assert result.revision_count == 1
    assert len([item for item in result.executions if item.agent is AgentRole.SAR_WRITER]) == 2


@pytest.mark.asyncio
async def test_reviewer_timeout_records_review_unavailable(make_sar_input) -> None:
    runtime = _FakeRuntime(
        _outcomes(
            reviewers=((AgentExecutionStatus.DEGRADED, "agent_timeout", None),),
        )
    )

    result, _events = await _run(runtime, make_sar_input())

    assert result.review_status is AgentReviewStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_investigator_degradation_still_reaches_writer(make_sar_input) -> None:
    runtime = _FakeRuntime(
        _outcomes(evidence=(AgentExecutionStatus.DEGRADED, "agent_output_invalid", None))
    )

    result, _events = await _run(runtime, make_sar_input())

    assert result.content is not None
    assert runtime.inputs[AgentRole.SAR_WRITER]
    writer_input = json.loads(runtime.inputs[AgentRole.SAR_WRITER][0])
    assert writer_input["evidenceBrief"]["limitations"]


@pytest.mark.asyncio
async def test_writer_failure_is_terminal_and_skips_reviewer(make_sar_input) -> None:
    runtime = _FakeRuntime(
        _outcomes(writers=((AgentExecutionStatus.FAILED, "llm_non_retryable_error", None),))
    )

    result, _events = await _run(runtime, make_sar_input())

    assert result.review_status is AgentReviewStatus.WRITER_FAILED
    assert result.content is None
    assert runtime.inputs[AgentRole.COMPLIANCE_REVIEWER] == []


@pytest.mark.asyncio
async def test_degraded_reviewer_defers_to_passing_deterministic_checks(make_sar_input) -> None:
    runtime = _FakeRuntime(
        _outcomes(
            reviewers=((AgentExecutionStatus.DEGRADED, "agent_output_invalid", None),),
        )
    )

    result, _events = await _run(runtime, make_sar_input())

    assert result.checks is not None and result.checks.passed is True
    assert result.review_status is AgentReviewStatus.PASSED


@pytest.mark.asyncio
async def test_fabricated_citation_reaches_reviewer_then_is_grounded(make_sar_input) -> None:
    completed = AgentExecutionStatus.COMPLETED
    runtime = _FakeRuntime(
        _outcomes(
            writers=(
                (completed, None, _draft(fabricated=True)),
                (completed, None, _draft(fabricated=True)),
            ),
            reviewers=(
                (completed, None, _verdict(ReviewDecision.REVISE)),
                (completed, None, _verdict(ReviewDecision.REVISE)),
            ),
        )
    )
    config = _config()
    graph = build_agent_graph(runtime=runtime, config=config, prompts=_prompts())
    drafter = MultiAgentSarDrafter(
        graph=graph,
        config=config,
        prompts=_prompts(),
        budget=BudgetGuard(session_limit_usd=Decimal("1")),
    )

    events = [event async for event in drafter.draft(make_sar_input())]
    reviewer_payload = json.loads(runtime.inputs[AgentRole.COMPLIANCE_REVIEWER][0])
    terminal = events[-1].result

    assert reviewer_payload["draft"]["citedRegulations"] == ["99 FAKE 1"]
    assert reviewer_payload["deterministicChecks"]["fabricatedCitationIds"] == ["99 FAKE 1"]
    assert terminal is not None and terminal.status is SarDraftStatus.DRAFT
    assert terminal.structured is not None
    assert terminal.structured.cited_regulations == ()
    assert terminal.structured.claims[0].citation_ids == ()
    assert terminal.workflow == "multi_agent" and terminal.revision_count == 1


@pytest.mark.asyncio
async def test_writer_failure_drafter_emits_failed_terminal_result(make_sar_input) -> None:
    runtime = _FakeRuntime(
        _outcomes(writers=((AgentExecutionStatus.FAILED, "llm_non_retryable_error", None),))
    )
    config = _config()
    drafter = MultiAgentSarDrafter(
        graph=build_agent_graph(runtime=runtime, config=config, prompts=_prompts()),
        config=config,
        prompts=_prompts(),
        budget=BudgetGuard(session_limit_usd=Decimal("1")),
    )

    events = [event async for event in drafter.draft(make_sar_input())]

    assert events[-1].type is SarEventType.FAILED
    assert events[-1].result is not None
    assert events[-1].result.status is SarDraftStatus.FAILED
    assert events[-1].result.error_code == "llm_non_retryable_error"


def test_agent_drafter_factory_builds_independent_run_scoped_drafters() -> None:
    toolset = cast(EvidenceToolset, _FakeToolset())
    factory = build_agent_drafter_factory(
        client=cast(LlmClient, object()),
        catalog=_catalog(),
        config=None,
    )

    first = factory(toolset)
    second = factory(toolset)

    assert isinstance(first, MultiAgentSarDrafter)
    assert isinstance(second, MultiAgentSarDrafter)
    assert first is not second
    assert first._budget is not second._budget


def test_agent_drafter_factory_preflight_rejects_over_budget_configuration() -> None:
    config = _config(max_cost_usd_per_investigation=Decimal("0.000001"))
    factory = build_agent_drafter_factory(
        client=cast(LlmClient, object()),
        catalog=_catalog(),
        config=config,
    )

    with pytest.raises(AgentBudgetExceededError, match="worst-case"):
        factory(cast(EvidenceToolset, _FakeToolset()))


def test_agent_drafter_factory_rejects_daily_budget_before_graph_construction() -> None:
    """Worst-case daily spend is denied before any runtime/provider call can occur."""
    factory = build_agent_drafter_factory(
        client=cast(LlmClient, object()),
        catalog=_catalog(),
        config=_config(),
    )

    with pytest.raises(AgentBudgetExceededError, match="daily budget"):
        factory(
            cast(EvidenceToolset, _FakeToolset()),
            daily_limit_usd=Decimal("0.01"),
            daily_spent_usd=Decimal("0.01"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("graph", "expected_code"),
    [
        (_FaultingGraph(RuntimeError("synthetic graph fault")), "agent_workflow_error"),
        (_FaultingGraph(delay_s=0.05), "agent_workflow_timeout"),
    ],
)
async def test_drafter_normalizes_workflow_faults(
    make_sar_input,
    graph: _FaultingGraph,
    expected_code: str,
) -> None:
    config = _config(workflow_timeout_s=0.001)
    drafter = MultiAgentSarDrafter(
        graph=cast(AgentGraph, graph),
        config=config,
        prompts=_prompts(),
        budget=BudgetGuard(session_limit_usd=Decimal("1")),
    )

    events = [event async for event in drafter.draft(make_sar_input())]

    assert events[-1].result is not None
    assert events[-1].result.error_code == expected_code


@pytest.mark.asyncio
async def test_drafter_propagates_preflight_budget_refusal(make_sar_input) -> None:
    drafter = MultiAgentSarDrafter(
        graph=cast(
            AgentGraph,
            _FaultingGraph(AgentBudgetExceededError("synthetic budget refusal")),
        ),
        config=_config(),
        prompts=_prompts(),
        budget=BudgetGuard(session_limit_usd=Decimal("1")),
    )

    with pytest.raises(AgentBudgetExceededError, match="budget refusal"):
        _ = [event async for event in drafter.draft(make_sar_input())]

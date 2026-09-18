"""Agent drafter factory, budget, and workflow-fault tests."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from decimal import Decimal
from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from quality_gates import grounding_gate

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
from fraudlens_backend.agents.graph import AgentGraph, build_agent_graph
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
        gate=grounding_gate(),
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
        gate=grounding_gate(),
    )

    with pytest.raises(AgentBudgetExceededError, match="budget refusal"):
        _ = [event async for event in drafter.draft(make_sar_input())]

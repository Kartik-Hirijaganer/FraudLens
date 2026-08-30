"""Unit tests for the bounded multi-agent provider/tool execution loop."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from fraudlens_backend.agents.config import AgentRole, AgentsConfig, load_agents_config
from fraudlens_backend.agents.contracts import (
    AgentExecutionStatus,
    AgentToolCallStatus,
    EvidenceBrief,
)
from fraudlens_backend.agents.prompts import AgentPromptTemplate
from fraudlens_backend.agents.runtime import (
    AgentBudgetExceededError,
    AgentRuntime,
    estimate_workflow_max_cost_usd,
)
from fraudlens_backend.sar.budget import estimate_cost_usd
from fraudlens_backend.settings import find_config_dir
from fraudlens_llm import (
    Catalog,
    DataClass,
    GenerationParams,
    GuardrailDecision,
    GuardrailError,
    GuardrailReport,
    LlmClient,
    LlmError,
    LlmMessage,
    LlmResult,
    LlmSettings,
    LlmUsage,
    MaskingReport,
    ModelCard,
    PhiMaskingMode,
    ProviderError,
    Role,
    ScanOutcome,
    Strictness,
    TaskType,
    ToolCall,
    ToolDefinition,
    load_catalog,
    load_providers,
)
from fraudlens_llm.adapters.base import AdapterGenerateResult

_TOOL_NAMES = {
    "transaction_history",
    "rule_hits",
    "shap_drivers",
    "alert_history",
    "regulation_search",
}


class _ToolOutput(BaseModel):
    """Structured fake tool output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str = Field(..., description="Synthetic record identifier.")
    note: str = Field(..., description="Synthetic result note.")


class _FakeClient:
    """Queue-backed fake matching the runtime's guarded client protocol."""

    def __init__(
        self,
        outcomes: Sequence[LlmResult | LlmError | Exception],
        *,
        delay_s: float = 0,
    ) -> None:
        self.outcomes = list(outcomes)
        self.delay_s = delay_s
        self.calls: list[dict[str, object]] = []
        self.messages: list[list[LlmMessage | dict[str, object]]] = []

    async def generate(
        self,
        messages: Sequence[LlmMessage | dict[str, object]],
        *,
        model: str | None = None,
        overrides: GenerationParams | None = None,
        task_type: TaskType = TaskType.GENERATION,
        data_class: DataClass | None = None,
        include_raw: bool = False,
        fallbacks: Sequence[str] | None = None,
        tools: Sequence[ToolDefinition] | None = None,
        tool_choice: str | None = None,
        response_schema: dict[str, object] | None = None,
        capture_undeclared_tool_calls: bool = False,
    ) -> LlmResult:
        self.calls.append(
            {
                "model": model,
                "overrides": overrides,
                "task_type": task_type,
                "data_class": data_class,
                "include_raw": include_raw,
                "fallbacks": fallbacks,
                "tools": tools,
                "tool_choice": tool_choice,
                "response_schema": response_schema,
                "capture_undeclared_tool_calls": capture_undeclared_tool_calls,
            }
        )
        self.messages.append(list(messages))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _QueuedAdapter:
    """Queue-backed provider seam for exercising the production LLM client boundary."""

    def __init__(self, outcomes: Sequence[AdapterGenerateResult]) -> None:
        self.outcomes = list(outcomes)
        self.generate_calls: list[Sequence[LlmMessage]] = []

    async def generate(
        self,
        *,
        model_id: str,
        card: ModelCard,
        messages: Sequence[LlmMessage],
        params: GenerationParams,
        tools: Sequence[ToolDefinition] = (),
        tool_choice: str | None = None,
        response_schema: dict[str, object] | None = None,
    ) -> AdapterGenerateResult:
        _ = (model_id, card, params, tools, tool_choice, response_schema)
        self.generate_calls.append(messages)
        return self.outcomes.pop(0)


def _catalog() -> Catalog:
    return load_catalog(find_config_dir() / "llm" / "catalog.yml")


def _config(**workflow_updates: object) -> AgentsConfig:
    config = load_agents_config(catalog=_catalog(), available_tools=_TOOL_NAMES)
    if not workflow_updates:
        return config
    workflow = config.workflow.model_copy(update=workflow_updates)
    return config.model_copy(update={"workflow": workflow})


def _definitions() -> dict[str, ToolDefinition]:
    schema = {
        "type": "object",
        "properties": {"record_id": {"type": "string"}},
        "required": ["record_id"],
        "additionalProperties": False,
    }
    return {
        name: ToolDefinition(
            name=name,
            description="Read a governed synthetic record by identifier.",
            parameters=schema,
        )
        for name in _TOOL_NAMES
    }


def _guardrail(decision: GuardrailDecision = GuardrailDecision.ALLOW) -> GuardrailReport:
    allow = ScanOutcome(decision=GuardrailDecision.ALLOW, findings=[])
    flagged = ScanOutcome(decision=GuardrailDecision.FLAG, findings=[])
    return GuardrailReport(
        decision=decision,
        strictness=Strictness.BLOCK,
        masking=MaskingReport(mode=PhiMaskingMode.ENFORCE, counts={}, total_masked=0),
        prompt_risk=flagged if decision is GuardrailDecision.FLAG else allow,
        output=allow,
        phishing=allow,
        policy=allow,
    )


def _evidence_json() -> str:
    return EvidenceBrief(
        summary="Deterministic indicators warrant human review.",
        findings=(
            {
                "statement": "The supplied amount pattern is notable.",
                "evidence_refs": ("rule-hit:structuring",),
            },
        ),
    ).model_dump_json(by_alias=True)


def _result(
    *,
    text: str = "",
    tool_calls: tuple[ToolCall, ...] = (),
    model: str = "openrouter/x-ai/grok-4.3",
    decision: GuardrailDecision = GuardrailDecision.ALLOW,
    usage: LlmUsage | None = None,
) -> LlmResult:
    return LlmResult(
        safe_text=text,
        model=model,
        provider="openrouter",
        usage=usage or LlmUsage(input_tokens=10, output_tokens=20, total_tokens=30),
        tool_calls=tool_calls,
        guardrail=_guardrail(decision),
    )


def _tool_call(
    call_id: str,
    *,
    name: str = "transaction_history",
    arguments: dict[str, object] | None = None,
) -> ToolCall:
    return ToolCall(
        id=call_id,
        name=name,
        arguments=arguments or {"record_id": f"record-{call_id}"},
    )


def _runtime(
    client: LlmClient | _FakeClient,
    *,
    config: AgentsConfig | None = None,
    executor_calls: list[tuple[str, dict[str, object]]] | None = None,
    executor_error: bool = False,
) -> AgentRuntime:
    async def execute_tool(name: str, arguments: dict[str, object]) -> BaseModel:
        if executor_calls is not None:
            executor_calls.append((name, arguments))
        if executor_error:
            raise RuntimeError("synthetic tool fault")
        return _ToolOutput(
            record_id=str(arguments["record_id"]),
            note="Contact analyst@example.com for context.",
        )

    return AgentRuntime(
        client=client,
        catalog=_catalog(),
        config=config or _config(),
        tool_definitions=_definitions(),
        tool_executor=execute_tool,
    )


async def _execute(runtime: AgentRuntime):
    return await runtime.execute(
        agent=AgentRole.EVIDENCE_INVESTIGATOR,
        prompt=AgentPromptTemplate.load(AgentRole.EVIDENCE_INVESTIGATOR, "v1"),
        user_content="Assess the supplied synthetic transaction.",
        response_model=EvidenceBrief,
    )


@pytest.mark.asyncio
async def test_final_response_on_first_turn_is_structured_and_costed() -> None:
    client = _FakeClient([_result(text=_evidence_json())])

    record = await _execute(_runtime(client))

    assert record.status is AgentExecutionStatus.COMPLETED
    assert record.result is not None and record.result["summary"].startswith("Deterministic")
    assert record.result_hash and record.input_hash
    assert record.model_call_count == 1
    assert record.input_tokens == 10 and record.output_tokens == 20
    assert client.calls[0]["task_type"] is TaskType.ANALYSIS
    assert client.calls[0]["tool_choice"] == "auto"
    assert client.calls[0]["capture_undeclared_tool_calls"] is True
    assert "evidenceRefs" in str(client.calls[0]["response_schema"])


@pytest.mark.asyncio
async def test_bounded_loop_executes_tools_masks_results_and_then_parses_final() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    client = _FakeClient(
        [
            _result(tool_calls=(_tool_call("1"),)),
            _result(tool_calls=(_tool_call("2", name="rule_hits"),)),
            _result(text=_evidence_json()),
        ]
    )

    record = await _execute(_runtime(client, executor_calls=calls))

    assert record.status is AgentExecutionStatus.COMPLETED
    assert len(record.tool_calls) == 2
    assert all(item.status is AgentToolCallStatus.COMPLETED for item in record.tool_calls)
    assert [name for name, _arguments in calls] == ["transaction_history", "rule_hits"]
    assert record.model_call_count == 3
    assert record.input_tokens == 30 and record.total_tokens == 90
    assert record.tool_calls[0].result is not None
    assert "analyst@example.com" not in str(record.tool_calls[0].result)
    second_messages = client.messages[1]
    tool_message = second_messages[-1]
    assert isinstance(tool_message, LlmMessage) and tool_message.role is Role.TOOL
    assert tool_message.content is not None and "<<AGENT_TOOL_DATA" in tool_message.content
    assert "analyst@example.com" not in tool_message.content


@pytest.mark.asyncio
async def test_unlisted_tool_is_refused_recorded_and_loop_continues() -> None:
    executor_calls: list[tuple[str, dict[str, object]]] = []
    client = _FakeClient(
        [
            _result(tool_calls=(_tool_call("1", name="other"),)),
            _result(text=_evidence_json()),
        ]
    )

    record = await _execute(_runtime(client, executor_calls=executor_calls))

    assert record.status is AgentExecutionStatus.DEGRADED
    assert record.error_code == "unauthorized_tool_call"
    assert record.result is not None
    assert record.tool_calls[0].status is AgentToolCallStatus.REFUSED
    assert executor_calls == []
    refusal = client.messages[1][-1]
    assert isinstance(refusal, LlmMessage) and "unauthorized_tool_call" in str(refusal.content)


@pytest.mark.asyncio
async def test_guarded_client_boundary_preserves_unlisted_tool_for_runtime_refusal() -> None:
    catalog = _catalog()
    client = LlmClient.from_config(
        catalog,
        load_providers(find_config_dir() / "llm" / "providers.yml"),
        LlmSettings(
            environment="dev",
            default_model="openrouter/x-ai/grok-4.3",
            phi_masking_mode=PhiMaskingMode.ENFORCE,
        ),
    )
    adapter = _QueuedAdapter(
        (
            AdapterGenerateResult(
                text="",
                served_model="x-ai/grok-4.3",
                finish_reason="tool_calls",
                usage=LlmUsage(input_tokens=10, output_tokens=20, total_tokens=30),
                tool_calls=(
                    _tool_call(
                        "1",
                        name="other",
                        arguments={
                            "record_id": "record-1",
                            "contact": "analyst@example.com",
                        },
                    ),
                ),
            ),
            AdapterGenerateResult(
                text=_evidence_json(),
                served_model="x-ai/grok-4.3",
                finish_reason="stop",
                usage=LlmUsage(input_tokens=20, output_tokens=20, total_tokens=40),
            ),
        )
    )
    client._adapters["openrouter"] = adapter
    executor_calls: list[tuple[str, dict[str, object]]] = []

    record = await _execute(_runtime(client, executor_calls=executor_calls))

    assert record.status is AgentExecutionStatus.DEGRADED
    assert record.error_code == "unauthorized_tool_call"
    assert record.tool_calls[0].arguments["contact"] == "[REDACTED_EMAIL]"
    assert executor_calls == []
    assert len(adapter.generate_calls) == 2
    assert "unauthorized_tool_call" in str(adapter.generate_calls[1][-1].content)


@pytest.mark.asyncio
async def test_tool_call_limit_is_structural_and_stops_provider_access() -> None:
    excessive = tuple(_tool_call(str(index)) for index in range(7))
    client = _FakeClient([_result(tool_calls=excessive)])

    record = await _execute(_runtime(client))

    assert record.status is AgentExecutionStatus.DEGRADED
    assert record.error_code == "tool_call_limit_exceeded"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_invalid_tool_arguments_and_tool_failure_degrade_but_continue() -> None:
    invalid_client = _FakeClient(
        [
            _result(tool_calls=(_tool_call("1", arguments={"record_id": 3}),)),
            _result(text=_evidence_json()),
        ]
    )
    failed_client = _FakeClient(
        [
            _result(tool_calls=(_tool_call("1"),)),
            _result(text=_evidence_json()),
        ]
    )

    invalid = await _execute(_runtime(invalid_client))
    failed = await _execute(_runtime(failed_client, executor_error=True))

    assert invalid.error_code == "invalid_tool_arguments"
    assert invalid.tool_calls[0].status is AgentToolCallStatus.REFUSED
    assert failed.error_code == "tool_unavailable"
    assert failed.tool_calls[0].status is AgentToolCallStatus.FAILED
    assert invalid.result is not None and failed.result is not None


@pytest.mark.asyncio
async def test_malformed_json_degrades_without_leaking_validation_details() -> None:
    record = await _execute(_runtime(_FakeClient([_result(text="not-json")])))

    assert record.status is AgentExecutionStatus.DEGRADED
    assert record.error_code == "agent_output_invalid"
    assert record.result is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (
            ProviderError("transient provider detail", retryable=True),
            AgentExecutionStatus.DEGRADED,
            "llm_retryable_error",
        ),
        (
            GuardrailError("sensitive validation detail"),
            AgentExecutionStatus.FAILED,
            "llm_non_retryable_error",
        ),
    ],
)
async def test_retryable_and_non_retryable_llm_errors_are_distinct_and_safe(
    error: LlmError,
    status: AgentExecutionStatus,
    code: str,
) -> None:
    record = await _execute(_runtime(_FakeClient([error])))

    assert record.status is status
    assert record.error_code == code
    assert "detail" not in record.model_dump_json()


@pytest.mark.asyncio
async def test_timeout_is_degraded_and_never_a_runtime_failure() -> None:
    client = _FakeClient([_result(text=_evidence_json())], delay_s=0.05)

    record = await _execute(_runtime(client, config=_config(agent_timeout_s=0.001)))

    assert record.status is AgentExecutionStatus.DEGRADED
    assert record.error_code == "agent_timeout"


@pytest.mark.asyncio
async def test_guardrail_flag_is_recorded_without_discarding_usable_output() -> None:
    client = _FakeClient([_result(text=_evidence_json(), decision=GuardrailDecision.FLAG)])

    record = await _execute(_runtime(client))

    assert record.status is AgentExecutionStatus.COMPLETED
    assert record.guardrail_decision is GuardrailDecision.FLAG


@pytest.mark.asyncio
async def test_cost_uses_the_served_fallback_reference() -> None:
    usage = LlmUsage(input_tokens=1000, output_tokens=500, total_tokens=1500)
    served_ref = "openrouter/google/gemini-2.5-flash"
    client = _FakeClient([_result(text=_evidence_json(), model=served_ref, usage=usage)])

    record = await _execute(_runtime(client))

    _provider, _model_id, served_card = _catalog().get(served_ref)
    assert record.model_id == served_ref
    assert record.cost_usd == estimate_cost_usd(served_card, usage)


@pytest.mark.asyncio
async def test_preflight_estimate_denies_before_any_provider_call() -> None:
    client = _FakeClient([_result(text=_evidence_json())])
    config = _config(max_cost_usd_per_investigation=Decimal("0.000001"))
    estimate = estimate_workflow_max_cost_usd(config, _catalog())

    assert estimate > config.workflow.max_cost_usd_per_investigation
    with pytest.raises(AgentBudgetExceededError, match="worst-case"):
        await _execute(_runtime(client, config=config))
    assert client.calls == []


def test_preflight_estimate_includes_bounded_writer_and_reviewer_revision() -> None:
    without_revision = estimate_workflow_max_cost_usd(_config(max_revisions=0), _catalog())
    with_revision = estimate_workflow_max_cost_usd(_config(max_revisions=1), _catalog())

    assert with_revision > without_revision


def test_runtime_constructor_fails_closed_for_incomplete_tool_binding() -> None:
    client = _FakeClient([_result(text=_evidence_json())])
    definitions = _definitions()
    definitions.pop("rule_hits")
    with pytest.raises(ValueError, match="missing"):
        AgentRuntime(
            client=client,
            catalog=_catalog(),
            config=_config(),
            tool_definitions=definitions,
            tool_executor=None,
        )

    mismatched = _definitions()
    mismatched["rule_hits"] = mismatched["transaction_history"]
    with pytest.raises(ValueError, match="keys"):
        AgentRuntime(
            client=client,
            catalog=_catalog(),
            config=_config(),
            tool_definitions=mismatched,
            tool_executor=lambda _name, _arguments: asyncio.sleep(0),
        )


@pytest.mark.asyncio
async def test_prompt_role_mismatch_is_rejected_before_provider_access() -> None:
    client = _FakeClient([_result(text=_evidence_json())])
    runtime = _runtime(client)

    with pytest.raises(ValueError, match="Prompt role"):
        await runtime.execute(
            agent=AgentRole.EVIDENCE_INVESTIGATOR,
            prompt=AgentPromptTemplate.load(AgentRole.REGULATORY_ANALYST, "v1"),
            user_content="synthetic",
            response_model=EvidenceBrief,
        )
    assert client.calls == []

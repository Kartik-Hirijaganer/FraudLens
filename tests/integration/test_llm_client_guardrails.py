"""LLM client masking, tools, streaming, and guardrail integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest

from fraudlens_llm import (
    CapabilityMismatchError,
    Catalog,
    DataClass,
    GenerationParams,
    GuardrailDecision,
    GuardrailError,
    Kind,
    Lifecycle,
    LlmClient,
    LlmMessage,
    LlmSettings,
    LlmUsage,
    ModelCard,
    PhiMaskingMode,
    Protocol,
    ProviderConfig,
    Providers,
    Role,
    StreamGenerationRequest,
    TaskType,
    ToolCall,
    ToolDefinition,
)
from fraudlens_llm.adapters.base import (
    AdapterEmbeddingResult,
    AdapterGenerateChunk,
    AdapterGenerateResult,
)
from fraudlens_llm.exceptions import LlmTimeoutError


class _FakeAdapter:
    def __init__(
        self,
        *,
        text: str = "safe response",
        fail_once: bool = False,
        embeddings: list[list[float]] | None = None,
        tool_calls: tuple[ToolCall, ...] = (),
    ) -> None:
        self.text = text
        self.fail_once = fail_once
        self.embeddings = embeddings or [[0.1, 0.2]]
        self.tool_calls = tool_calls
        self.generate_calls: list[Sequence[LlmMessage]] = []
        self.stream_generate_calls: list[Sequence[LlmMessage]] = []
        self.embed_calls: list[Sequence[str]] = []
        self.params: list[GenerationParams] = []
        self.tools: list[Sequence[ToolDefinition]] = []
        self.tool_choices: list[str | None] = []
        self.response_schemas: list[dict[str, object] | None] = []

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
        _ = (model_id, card)
        self.generate_calls.append(messages)
        self.params.append(params)
        self.tools.append(tools)
        self.tool_choices.append(tool_choice)
        self.response_schemas.append(response_schema)
        if self.fail_once:
            self.fail_once = False
            raise LlmTimeoutError("timeout")
        return AdapterGenerateResult(
            text=self.text,
            served_model="served",
            finish_reason="stop",
            usage=LlmUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            tool_calls=self.tool_calls,
        )

    async def generate_stream(
        self,
        *,
        model_id: str,
        card: ModelCard,
        messages: Sequence[LlmMessage],
        params: GenerationParams,
    ) -> AsyncIterator[AdapterGenerateChunk]:
        _ = (model_id, card)
        self.stream_generate_calls.append(messages)
        self.params.append(params)
        if self.fail_once:
            self.fail_once = False
            raise LlmTimeoutError("timeout")
        midpoint = len(self.text) // 2
        yield AdapterGenerateChunk(text_delta=self.text[:midpoint], served_model="served")
        yield AdapterGenerateChunk(
            text_delta=self.text[midpoint:],
            served_model="served",
            finish_reason="stop",
            usage=LlmUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        )

    async def embed(
        self,
        *,
        model_id: str,
        card: ModelCard,
        inputs: Sequence[str],
        params: GenerationParams,
    ) -> AdapterEmbeddingResult:
        _ = (model_id, card, params)
        self.embed_calls.append(inputs)
        return AdapterEmbeddingResult(
            embeddings=self.embeddings,
            usage=LlmUsage(input_tokens=3, output_tokens=0, total_tokens=3),
        )


def _model_card(
    kind: Kind,
    *,
    callable_value: bool = True,
    tool_calling: bool = False,
    structured_output: bool = False,
) -> ModelCard:
    return ModelCard(
        kind=kind,
        context_window=1000,
        default_params=GenerationParams(temperature=0.1, max_tokens=50)
        if kind == Kind.CHAT
        else GenerationParams(dimensions=2),
        input_price_per_million=1.0,
        output_price_per_million=2.0,
        source_url="https://example.com",
        verified_at="2026-06-10",
        lifecycle=Lifecycle.GA,
        callable=callable_value,
        tool_calling=tool_calling,
        structured_output=structured_output,
        pricing_basis="per_million_tokens",
    )


def _provider(
    *,
    protocol: Protocol = Protocol.OPENAI_COMPATIBLE,
    allowed: list[DataClass] | None = None,
    region: str = "us",
    retention: str = "30d",
    zdr: bool = True,
    training: bool = True,
) -> ProviderConfig:
    return ProviderConfig(
        protocol=protocol,
        base_url="https://example.com/v1" if protocol == Protocol.OPENAI_COMPATIBLE else None,
        api_key_env="EXAMPLE_API_KEY",
        timeout_s=10,
        max_retries=0,
        region=region,
        data_retention=retention,
        zdr_supported=zdr,
        training_opt_out=training,
        baa_required=False,
        allowed_data_classes=allowed or [DataClass.SYNTHETIC, DataClass.DEIDENTIFIED],
    )


def _client(
    *,
    settings: LlmSettings | None = None,
    include_openrouter: bool = True,
) -> LlmClient:
    catalog = Catalog(
        providers={
            "openai": {
                "chat": _model_card(
                    Kind.CHAT,
                    tool_calling=True,
                    structured_output=True,
                ),
                "embed": _model_card(Kind.EMBED),
                "disabled": _model_card(Kind.CHAT, callable_value=False),
            },
            "anthropic": {
                "chat": _model_card(
                    Kind.CHAT,
                    tool_calling=True,
                    structured_output=True,
                )
            },
            "ollama": {"llama": _model_card(Kind.CHAT)},
            **({"openrouter": {"chat": _model_card(Kind.CHAT)}} if include_openrouter else {}),
        }
    )
    providers = Providers(
        providers={
            "openai": _provider(),
            "anthropic": _provider(protocol=Protocol.ANTHROPIC),
            **(
                {
                    "openrouter": _provider(
                        region="global",
                        retention="provider-default",
                        zdr=False,
                        training=False,
                    )
                }
                if include_openrouter
                else {}
            ),
        }
    )
    return LlmClient.from_config(
        catalog,
        providers,
        settings
        or LlmSettings(
            environment="dev",
            default_model="openai/chat",
            phi_masking_mode=PhiMaskingMode.ENFORCE,
        ),
    )


def _tool() -> ToolDefinition:
    return ToolDefinition(
        name="transaction_history",
        description="Read transaction history by identifier.",
        parameters={
            "type": "object",
            "properties": {"transaction_id": {"type": "string"}},
            "required": ["transaction_id"],
            "additionalProperties": False,
        },
    )


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_generate_masks_phi_before_fake_adapter_and_excludes_raw_by_default() -> None:
    client = _client()
    fake = _FakeAdapter()
    client._adapters["openai"] = fake

    result = await client.generate(
        [LlmMessage(role=Role.USER, content="contact a@example.com and SSN 123-45-6789")],
        model="openai/chat",
        overrides=GenerationParams(max_tokens=7),
    )

    provider_messages = fake.generate_calls[0]
    combined = "\n".join(message.content for message in provider_messages)
    assert "a@example.com" not in combined
    assert "123-45-6789" not in combined
    assert "[REDACTED_EMAIL]" in combined
    assert result.safe_text == "safe response"
    assert result.raw_text is None
    assert result.guardrail.masking.total_masked == 2
    assert fake.params[0].max_tokens == 7


@pytest.mark.asyncio
async def test_generate_round_trips_tools_schema_and_masks_tool_surfaces() -> None:
    client = _client()
    fake = _FakeAdapter(
        text="",
        tool_calls=(
            ToolCall(
                id="call-2",
                name="transaction_history",
                arguments={"transaction_id": "a@example.com"},
            ),
        ),
    )
    client._adapters["openai"] = fake
    prior_call = ToolCall(
        id="call-1",
        name="transaction_history",
        arguments={"transaction_id": "txn-1"},
    )
    response_schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
    }

    result = await client.generate(
        [
            LlmMessage(role=Role.USER, content="Analyze the transaction."),
            LlmMessage(role=Role.ASSISTANT, tool_calls=(prior_call,)),
            LlmMessage(
                role=Role.TOOL,
                tool_call_id="call-1",
                content='{"contact":"b@example.com"}',
            ),
        ],
        model="openai/chat",
        tools=[_tool()],
        tool_choice="transaction_history",
        response_schema=response_schema,
        task_type=TaskType.ANALYSIS,
    )

    provider_messages = fake.generate_calls[0]
    assert provider_messages[-1].content == '{"contact":"[REDACTED_EMAIL]"}'
    assert fake.tools[0] == (_tool(),)
    assert fake.tool_choices[0] == "transaction_history"
    assert fake.response_schemas[0] == response_schema
    assert result.tool_calls[0].arguments == {"transaction_id": "[REDACTED_EMAIL]"}
    assert result.guardrail.masking.total_masked == 2


@pytest.mark.asyncio
async def test_undeclared_tool_calls_require_explicit_capture_for_refusal_workflows() -> None:
    client = _client()
    undeclared = ToolCall(
        id="call-undeclared",
        name="other",
        arguments={"contact": "analyst@example.com"},
    )
    fake = _FakeAdapter(text="", tool_calls=(undeclared,))
    client._adapters["openai"] = fake

    with pytest.raises(GuardrailError, match="undeclared tool"):
        await client.generate(
            [LlmMessage(role=Role.USER, content="Analyze the transaction.")],
            model="openai/chat",
            tools=[_tool()],
            task_type=TaskType.ANALYSIS,
        )

    captured = await client.generate(
        [LlmMessage(role=Role.USER, content="Analyze the transaction.")],
        model="openai/chat",
        tools=[_tool()],
        task_type=TaskType.ANALYSIS,
        capture_undeclared_tool_calls=True,
    )

    assert captured.tool_calls[0].name == "other"
    assert captured.tool_calls[0].arguments == {"contact": "[REDACTED_EMAIL]"}

    fake.text = "safe response"
    fake.tool_calls = ()
    follow_up = await client.generate(
        [
            LlmMessage(role=Role.ASSISTANT, tool_calls=captured.tool_calls),
            LlmMessage(
                role=Role.TOOL,
                tool_call_id="call-undeclared",
                content="unauthorized_tool_call",
            ),
        ],
        model="openai/chat",
        tools=[_tool()],
        task_type=TaskType.ANALYSIS,
        capture_undeclared_tool_calls=True,
    )

    assert follow_up.safe_text == "safe response"
    assert fake.generate_calls[-1][1].tool_calls[0].arguments == {"contact": "[REDACTED_EMAIL]"}


@pytest.mark.asyncio
async def test_tool_arguments_fail_closed_before_provider_access() -> None:
    client = _client()
    fake = _FakeAdapter()
    client._adapters["openai"] = fake

    for arguments in (
        {"transaction_id": 7},
        {"transaction_id": "http://127.0.0.1/private"},
        {"transaction_id": "file:///etc/passwd"},
        {"transaction_id": "https://example.com/not-allowlisted"},
    ):
        with pytest.raises(GuardrailError):
            await client.generate(
                [
                    LlmMessage(
                        role=Role.ASSISTANT,
                        tool_calls=(
                            ToolCall(
                                id="call-1",
                                name="transaction_history",
                                arguments=arguments,
                            ),
                        ),
                    )
                ],
                model="openai/chat",
                tools=[_tool()],
                task_type=TaskType.ANALYSIS,
            )

    assert fake.generate_calls == []


@pytest.mark.asyncio
async def test_tool_and_structured_capabilities_include_fallbacks() -> None:
    client = _client()
    fake = _FakeAdapter()
    client._adapters["openai"] = fake

    with pytest.raises(CapabilityMismatchError, match="openrouter/chat"):
        await client.generate(
            [LlmMessage(role=Role.USER, content="Analyze the transaction.")],
            model="openai/chat",
            tools=[_tool()],
            response_schema={"type": "object"},
            fallbacks=["openrouter/chat"],
            task_type=TaskType.ANALYSIS,
        )
    assert fake.generate_calls == []


@pytest.mark.asyncio
async def test_primary_model_capability_mismatch_fails_before_provider_access() -> None:
    client = _client()
    fake = _FakeAdapter()
    client._adapters["openrouter"] = fake

    with pytest.raises(CapabilityMismatchError, match="tool calling"):
        await client.generate(
            [LlmMessage(role=Role.USER, content="Analyze the transaction.")],
            model="openrouter/chat",
            tools=[_tool()],
            task_type=TaskType.ANALYSIS,
        )
    with pytest.raises(CapabilityMismatchError, match="structured output"):
        await client.generate(
            [LlmMessage(role=Role.USER, content="Analyze the transaction.")],
            model="openrouter/chat",
            response_schema={"type": "object"},
            task_type=TaskType.ANALYSIS,
        )
    assert fake.generate_calls == []


@pytest.mark.asyncio
async def test_generate_stream_assembles_ordered_deltas_before_output_guardrails() -> None:
    client = _client()
    fake = _FakeAdapter(text='Analysis: safe <img onerror="x"> response')
    client._adapters["openai"] = fake

    result = await client.generate_stream(
        StreamGenerationRequest(
            messages=[LlmMessage(role=Role.USER, content="contact a@example.com")],
            model="openai/chat",
            task_type=TaskType.ANALYSIS,
        )
    )

    combined = "\n".join(message.content for message in fake.stream_generate_calls[0])
    assert "a@example.com" not in combined
    assert result.safe_text == "Analysis: safe <img> response"
    assert result.guardrail.decision == GuardrailDecision.FLAG
    assert result.usage.total_tokens == 15
    assert result.finish_reason == "stop"

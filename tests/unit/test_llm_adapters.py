"""Unit tests for private provider adapters with mocked SDK boundaries."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from anthropic import (
    AnthropicError,
)
from anthropic import (
    APIConnectionError as AnthropicConnectionError,
)
from anthropic import (
    APIStatusError as AnthropicStatusError,
)
from anthropic import (
    APITimeoutError as AnthropicTimeoutError,
)
from anthropic import (
    AuthenticationError as AnthropicAuthenticationError,
)
from anthropic import (
    BadRequestError as AnthropicBadRequestError,
)
from anthropic import (
    RateLimitError as AnthropicRateLimitError,
)
from openai import (
    APIConnectionError as OpenAiConnectionError,
)
from openai import (
    APIStatusError as OpenAiStatusError,
)
from openai import (
    APITimeoutError as OpenAiTimeoutError,
)
from openai import (
    AuthenticationError as OpenAiAuthenticationError,
)
from openai import (
    BadRequestError as OpenAiBadRequestError,
)
from openai import (
    OpenAIError,
)
from openai import (
    RateLimitError as OpenAiRateLimitError,
)

import fraudlens_llm.adapters.anthropic as anthropic_module
import fraudlens_llm.adapters.openai_compatible as openai_module
from fraudlens_llm import (
    CapabilityMismatchError,
    DataClass,
    GenerationParams,
    Kind,
    LlmMessage,
    MissingApiKeyError,
    ModelCard,
    Protocol,
    ProviderConfig,
    Role,
    ToolCall,
    ToolDefinition,
    UnsupportedParameterError,
)
from fraudlens_llm.adapters.anthropic import AnthropicAdapter
from fraudlens_llm.adapters.openai_compatible import OpenAiCompatibleAdapter
from fraudlens_llm.exceptions import (
    LlmError,
    LlmRateLimitError,
    LlmTimeoutError,
    ProviderAuthError,
    ProviderError,
)


def _provider_config(protocol: Protocol = Protocol.OPENAI_COMPATIBLE) -> ProviderConfig:
    return ProviderConfig(
        protocol=protocol,
        base_url="https://example.com/v1" if protocol == Protocol.OPENAI_COMPATIBLE else None,
        api_key_env="EXAMPLE_API_KEY",
        timeout_s=10,
        max_retries=0,
        region="us",
        data_retention="none",
        zdr_supported=True,
        training_opt_out=True,
        baa_required=False,
        allowed_data_classes=[DataClass.SYNTHETIC],
    )


def _card(kind: Kind = Kind.CHAT) -> ModelCard:
    return ModelCard(
        kind=kind,
        context_window=100,
        default_params=(
            GenerationParams(temperature=0.1) if kind == Kind.CHAT else GenerationParams()
        ),
        source_url="https://example.com",
        verified_at="2026-06-10",
        lifecycle="ga",
        callable=True,
        pricing_basis="per_million_tokens",
    )


class _OpenAiChatCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if kwargs.get("stream") is True:
            return _OpenAiStream(
                [
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(content="adapter "),
                                finish_reason=None,
                            )
                        ],
                        usage=None,
                        model="served-chat",
                    ),
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(content="ok"),
                                finish_reason="stop",
                            )
                        ],
                        usage=None,
                        model="served-chat",
                    ),
                    SimpleNamespace(
                        choices=[],
                        usage=SimpleNamespace(
                            prompt_tokens=4,
                            completion_tokens=5,
                            total_tokens=9,
                        ),
                        model="served-chat",
                    ),
                ]
            )
        tool_calls = (
            [
                SimpleNamespace(
                    id="call-1",
                    function=SimpleNamespace(
                        name="transaction_history",
                        arguments='{"transaction_id":"txn-1"}',
                    ),
                )
            ]
            if kwargs.get("tools")
            else None
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None if tool_calls else "adapter ok",
                        tool_calls=tool_calls,
                    ),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=5, total_tokens=9),
            model="served-chat",
        )


class _OpenAiStream:
    def __init__(self, chunks: list[object]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> _OpenAiStream:
        return self

    async def __anext__(self) -> object:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class _OpenAiEmbeddings:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1, 0.2])],
            usage=SimpleNamespace(prompt_tokens=2, total_tokens=2),
        )


class _OpenAiClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_OpenAiChatCompletions())
        self.embeddings = _OpenAiEmbeddings()


class _AnthropicMessages:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        content = (
            [
                SimpleNamespace(
                    type="tool_use",
                    id="call-1",
                    name="transaction_history",
                    input={"transaction_id": "txn-1"},
                )
            ]
            if kwargs.get("tools")
            else [SimpleNamespace(type="text", text="anthropic ok")]
        )
        return SimpleNamespace(
            content=content,
            usage=SimpleNamespace(input_tokens=3, output_tokens=4),
            model="served-anthropic",
            stop_reason="end_turn",
        )


class _AnthropicClient:
    def __init__(self) -> None:
        self.messages = _AnthropicMessages()


def _request_response(status_code: int = 400) -> tuple[httpx.Request, httpx.Response]:
    request = httpx.Request("GET", "https://example.com/models")
    return request, httpx.Response(status_code, request=request)


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
async def test_openai_adapter_chat_and_embed_happy_paths() -> None:
    adapter = OpenAiCompatibleAdapter("openai", _provider_config())
    fake = _OpenAiClient()
    adapter._client = fake

    chat = await adapter.generate(
        model_id="gpt-5-mini",
        card=_card(),
        messages=[LlmMessage(role=Role.USER, content="hello")],
        params=GenerationParams(temperature=0.2, max_tokens=10, response_format="json_object"),
    )
    embed = await adapter.embed(
        model_id="text-embedding-3-small",
        card=_card(Kind.EMBED),
        inputs=["hello"],
        params=GenerationParams(dimensions=2),
    )
    stream = [
        chunk
        async for chunk in adapter.generate_stream(
            model_id="gpt-5-mini",
            card=_card(),
            messages=[LlmMessage(role=Role.USER, content="hello")],
            params=GenerationParams(max_tokens=10, response_format="json_object"),
        )
    ]

    assert chat.text == "adapter ok"
    assert chat.usage.total_tokens == 9
    assert fake.chat.completions.calls[0]["messages"] == [{"role": "user", "content": "hello"}]
    assert fake.chat.completions.calls[0]["response_format"] == {"type": "json_object"}
    assert embed.embeddings == [[0.1, 0.2]]
    assert fake.embeddings.calls[0]["dimensions"] == 2
    assert "".join(chunk.text_delta for chunk in stream) == "adapter ok"
    assert stream[-1].usage.total_tokens == 9
    assert fake.chat.completions.calls[1]["stream"] is True
    assert fake.chat.completions.calls[1]["stream_options"] == {"include_usage": True}
    assert fake.chat.completions.calls[1]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_openai_adapter_maps_tools_messages_and_structured_output() -> None:
    adapter = OpenAiCompatibleAdapter("openai", _provider_config())
    fake = _OpenAiClient()
    adapter._client = fake
    tool_call = ToolCall(
        id="call-0",
        name="transaction_history",
        arguments={"transaction_id": "txn-0"},
    )

    result = await adapter.generate(
        model_id="gpt-5-mini",
        card=_card(),
        messages=[
            LlmMessage(role=Role.USER, content="Find transaction context."),
            LlmMessage(role=Role.ASSISTANT, tool_calls=(tool_call,)),
            LlmMessage(role=Role.TOOL, tool_call_id="call-0", content='{"count":1}'),
        ],
        params=GenerationParams(max_tokens=10),
        tools=[_tool()],
        tool_choice="transaction_history",
        response_schema={
            "title": "Agent Result",
            "type": "object",
            "properties": {"summary": {"type": "string"}},
        },
    )

    call = fake.chat.completions.calls[0]
    assert call["messages"][1]["tool_calls"][0]["function"]["name"] == "transaction_history"
    assert call["messages"][2] == {
        "role": "tool",
        "content": '{"count":1}',
        "tool_call_id": "call-0",
    }
    assert call["tools"][0]["function"]["parameters"]["type"] == "object"
    assert call["tool_choice"] == {
        "type": "function",
        "function": {"name": "transaction_history"},
    }
    assert call["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "Agent_Result",
            "strict": True,
            "schema": {
                "title": "Agent Result",
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "additionalProperties": False,
                "required": ["summary"],
            },
        },
    }
    assert result.text == ""
    assert result.tool_calls[0].arguments == {"transaction_id": "txn-1"}


def test_openai_strict_schema_requires_every_nested_property_without_mutating_input() -> None:
    schema = {
        "title": "Nested Result",
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "default": [],
                "items": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                    },
                    "required": ["value"],
                },
            }
        },
        "required": [],
    }

    formatted = openai_module._structured_response_format(schema)
    strict = formatted["json_schema"]["schema"]

    assert strict["required"] == ["items"]
    assert strict["additionalProperties"] is False
    nested = strict["properties"]["items"]["items"]
    assert nested["required"] == ["value", "tags"]
    assert nested["additionalProperties"] is False
    assert "default" not in strict["properties"]["items"]
    assert "default" not in nested["properties"]["tags"]
    assert schema["required"] == []
    assert schema["properties"]["items"]["default"] == []


@pytest.mark.asyncio
async def test_openai_adapter_validates_key_capability_and_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = OpenAiCompatibleAdapter("openai", _provider_config())
    monkeypatch.delenv("EXAMPLE_API_KEY", raising=False)

    with pytest.raises(MissingApiKeyError):
        adapter._client_instance()
    with pytest.raises(CapabilityMismatchError):
        await adapter.generate(
            model_id="embed",
            card=_card(Kind.EMBED),
            messages=[LlmMessage(role=Role.USER, content="hello")],
            params=GenerationParams(),
        )
    with pytest.raises(UnsupportedParameterError):
        await adapter.embed(
            model_id="embed",
            card=_card(Kind.EMBED),
            inputs=["hello"],
            params=GenerationParams(max_tokens=3),
        )


@pytest.mark.asyncio
async def test_openai_adapter_maps_sdk_errors_from_calls() -> None:
    request, response = _request_response(429)
    adapter = OpenAiCompatibleAdapter("openai", _provider_config())
    fake = _OpenAiClient()

    async def raise_chat(**kwargs: object) -> object:
        _ = kwargs
        raise OpenAiRateLimitError("rate", response=response, body=None)

    async def raise_embed(**kwargs: object) -> object:
        _ = kwargs
        raise OpenAiConnectionError(request=request)

    fake.chat.completions.create = raise_chat
    fake.embeddings.create = raise_embed
    adapter._client = fake

    with pytest.raises(LlmRateLimitError):
        await adapter.generate(
            model_id="gpt-5-mini",
            card=_card(),
            messages=[LlmMessage(role=Role.USER, content="hello")],
            params=GenerationParams(),
        )
    with pytest.raises(ProviderError, match="connection"):
        await adapter.embed(
            model_id="text-embedding-3-small",
            card=_card(Kind.EMBED),
            inputs=["hello"],
            params=GenerationParams(),
        )


def test_openai_adapter_client_factory_and_normalizers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_openai(**kwargs: object) -> _OpenAiClient:
        captured.update(kwargs)
        return _OpenAiClient()

    monkeypatch.setenv("EXAMPLE_API_KEY", "test-key")
    monkeypatch.setattr(openai_module, "AsyncOpenAI", fake_openai)
    adapter = OpenAiCompatibleAdapter("openai", _provider_config())

    assert adapter._client_instance() is adapter._client
    assert captured["api_key"] == "test-key"
    assert openai_module._content_to_text([{"text": "a"}, SimpleNamespace(text="b")]) == "ab"
    assert openai_module._content_to_text(None) == ""
    assert openai_module._content_to_text(123) == "123"
    usage = openai_module._usage_from_openai(
        SimpleNamespace(usage=SimpleNamespace(input_tokens=1, output_tokens=2))
    )
    assert usage.total_tokens == 3


def test_provider_tool_call_parsers_fail_closed_on_malformed_blocks() -> None:
    with pytest.raises(ProviderError, match="malformed tool arguments"):
        openai_module._tool_calls_from_openai(
            [
                SimpleNamespace(
                    id="call-1",
                    function=SimpleNamespace(name="lookup", arguments="not-json"),
                )
            ]
        )
    with pytest.raises(ProviderError, match="invalid tool call"):
        anthropic_module._tool_calls_from_anthropic(
            [
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "lookup",
                    "input": "not-an-object",
                }
            ]
        )


def test_openai_error_mapping_ladder() -> None:
    request, bad_response = _request_response(400)
    _same_request, retry_response = _request_response(503)

    assert isinstance(
        openai_module._map_openai_error(OpenAiTimeoutError(request), "p"),
        LlmTimeoutError,
    )
    assert isinstance(
        openai_module._map_openai_error(
            OpenAiAuthenticationError("auth", response=bad_response, body=None),
            "p",
        ),
        ProviderAuthError,
    )
    assert isinstance(
        openai_module._map_openai_error(
            OpenAiBadRequestError("bad", response=bad_response, body=None),
            "p",
        ),
        ProviderError,
    )
    transient = openai_module._map_openai_error(
        OpenAiStatusError("status", response=retry_response, body=None),
        "p",
    )
    non_transient = openai_module._map_openai_error(
        OpenAiStatusError("status", response=bad_response, body=None),
        "p",
    )
    assert isinstance(transient, ProviderError) and transient.retryable is True
    assert isinstance(non_transient, ProviderError) and non_transient.retryable is False
    assert isinstance(openai_module._map_openai_error(OpenAIError("unknown"), "p"), LlmError)


@pytest.mark.asyncio
async def test_anthropic_adapter_chat_and_rejects_embed() -> None:
    adapter = AnthropicAdapter("anthropic", _provider_config(Protocol.ANTHROPIC))
    fake = _AnthropicClient()
    adapter._client = fake

    result = await adapter.generate(
        model_id="claude-sonnet",
        card=_card(),
        messages=[
            LlmMessage(role=Role.SYSTEM, content="policy"),
            LlmMessage(role=Role.USER, content="hello"),
        ],
        params=GenerationParams(temperature=0.2, max_tokens=10, stop=["END"]),
    )

    assert result.text == "anthropic ok"
    assert result.usage.total_tokens == 7
    call = fake.messages.calls[0]
    assert call["system"] == "policy"
    assert call["messages"] == [{"role": "user", "content": "hello"}]
    assert call["stop_sequences"] == ["END"]
    with pytest.raises(CapabilityMismatchError):
        await adapter.embed(
            model_id="claude-sonnet",
            card=_card(Kind.EMBED),
            inputs=["hello"],
            params=GenerationParams(),
        )


@pytest.mark.asyncio
async def test_anthropic_adapter_maps_tool_use_and_tool_result_blocks() -> None:
    adapter = AnthropicAdapter("anthropic", _provider_config(Protocol.ANTHROPIC))
    fake = _AnthropicClient()
    adapter._client = fake
    tool_call = ToolCall(
        id="call-0",
        name="transaction_history",
        arguments={"transaction_id": "txn-0"},
    )

    result = await adapter.generate(
        model_id="claude-sonnet",
        card=_card(),
        messages=[
            LlmMessage(role=Role.SYSTEM, content="policy"),
            LlmMessage(role=Role.ASSISTANT, content="Checking.", tool_calls=(tool_call,)),
            LlmMessage(role=Role.TOOL, tool_call_id="call-0", content='{"count":1}'),
        ],
        params=GenerationParams(max_tokens=10),
        tools=[_tool()],
        tool_choice="required",
        response_schema={"type": "object"},
    )

    call = fake.messages.calls[0]
    assert call["messages"][0]["content"][1] == {
        "type": "tool_use",
        "id": "call-0",
        "name": "transaction_history",
        "input": {"transaction_id": "txn-0"},
    }
    assert call["messages"][1] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call-0", "content": '{"count":1}'}],
    }
    assert call["tools"][0]["input_schema"]["type"] == "object"
    assert call["tool_choice"] == {"type": "any"}
    assert call["output_config"] == {
        "format": {"type": "json_schema", "schema": {"type": "object"}}
    }
    assert result.text == ""
    assert result.tool_calls == (
        ToolCall(
            id="call-1",
            name="transaction_history",
            arguments={"transaction_id": "txn-1"},
        ),
    )


@pytest.mark.asyncio
async def test_anthropic_adapter_validates_params() -> None:
    adapter = AnthropicAdapter("anthropic", _provider_config(Protocol.ANTHROPIC))
    adapter._client = _AnthropicClient()

    with pytest.raises(UnsupportedParameterError):
        await adapter.generate(
            model_id="claude-sonnet",
            card=_card(),
            messages=[LlmMessage(role=Role.USER, content="hello")],
            params=GenerationParams(response_format="json_object"),
        )


@pytest.mark.asyncio
async def test_anthropic_adapter_maps_sdk_errors_and_capability() -> None:
    request, response = _request_response(429)
    adapter = AnthropicAdapter("anthropic", _provider_config(Protocol.ANTHROPIC))
    fake = _AnthropicClient()

    async def raise_message(**kwargs: object) -> object:
        _ = kwargs
        raise AnthropicRateLimitError("rate", response=response, body=None)

    fake.messages.create = raise_message
    adapter._client = fake

    with pytest.raises(LlmRateLimitError):
        await adapter.generate(
            model_id="claude-sonnet",
            card=_card(),
            messages=[LlmMessage(role=Role.USER, content="hello")],
            params=GenerationParams(max_tokens=10),
        )
    with pytest.raises(CapabilityMismatchError):
        await adapter.generate(
            model_id="embed",
            card=_card(Kind.EMBED),
            messages=[LlmMessage(role=Role.USER, content="hello")],
            params=GenerationParams(max_tokens=10),
        )
    with pytest.raises(MissingApiKeyError):
        AnthropicAdapter("anthropic", _provider_config(Protocol.ANTHROPIC))._client_instance()
    assert request.url.host == "example.com"


def test_anthropic_adapter_client_factory_and_normalizers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, object]] = []

    def fake_anthropic(**kwargs: object) -> _AnthropicClient:
        created.append(kwargs)
        return _AnthropicClient()

    monkeypatch.setenv("EXAMPLE_API_KEY", "test-key")
    monkeypatch.setattr(anthropic_module, "AsyncAnthropic", fake_anthropic)
    AnthropicAdapter("anthropic", _provider_config(Protocol.ANTHROPIC))._client_instance()
    AnthropicAdapter(
        "anthropic",
        ProviderConfig(
            protocol=Protocol.ANTHROPIC,
            base_url="https://example.com/anthropic",
            api_key_env="EXAMPLE_API_KEY",
            timeout_s=10,
            max_retries=0,
            region="us",
            data_retention="none",
            zdr_supported=True,
            training_opt_out=True,
            baa_required=False,
            allowed_data_classes=[DataClass.SYNTHETIC],
        ),
    )._client_instance()

    assert created[0]["api_key"] == "test-key"
    assert created[1]["base_url"] == "https://example.com/anthropic"
    assert anthropic_module._content_to_text([{"text": "a"}, SimpleNamespace(text="b")]) == "ab"
    assert anthropic_module._content_to_text(None) == ""
    assert anthropic_module._content_to_text(123) == "123"


def test_anthropic_error_mapping_ladder() -> None:
    request, bad_response = _request_response(400)
    _same_request, retry_response = _request_response(503)

    assert isinstance(
        anthropic_module._map_anthropic_error(AnthropicTimeoutError(request), "p"),
        LlmTimeoutError,
    )
    assert isinstance(
        anthropic_module._map_anthropic_error(
            AnthropicAuthenticationError("auth", response=bad_response, body=None),
            "p",
        ),
        ProviderAuthError,
    )
    assert isinstance(
        anthropic_module._map_anthropic_error(
            AnthropicConnectionError(request=request),
            "p",
        ),
        ProviderError,
    )
    assert isinstance(
        anthropic_module._map_anthropic_error(
            AnthropicBadRequestError("bad", response=bad_response, body=None),
            "p",
        ),
        ProviderError,
    )
    transient = anthropic_module._map_anthropic_error(
        AnthropicStatusError("status", response=retry_response, body=None),
        "p",
    )
    non_transient = anthropic_module._map_anthropic_error(
        AnthropicStatusError("status", response=bad_response, body=None),
        "p",
    )
    assert isinstance(transient, ProviderError) and transient.retryable is True
    assert isinstance(non_transient, ProviderError) and non_transient.retryable is False
    assert isinstance(
        anthropic_module._map_anthropic_error(AnthropicError("unknown"), "p"),
        LlmError,
    )

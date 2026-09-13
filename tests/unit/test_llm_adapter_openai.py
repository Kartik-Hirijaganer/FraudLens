"""OpenAI-compatible LLM adapter contract, tool, schema, and error tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from llm_fakes import (
    _card,
    _OpenAiClient,
    _provider_config,
    _request_response,
    _tool,
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
    GenerationParams,
    Kind,
    LlmMessage,
    MissingApiKeyError,
    Role,
    ToolCall,
    UnsupportedParameterError,
)
from fraudlens_llm.adapters.openai_compatible import OpenAiCompatibleAdapter
from fraudlens_llm.exceptions import (
    LlmError,
    LlmRateLimitError,
    LlmTimeoutError,
    ProviderAuthError,
    ProviderError,
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
    assert str(captured["base_url"]) == "https://example.com/v1"
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

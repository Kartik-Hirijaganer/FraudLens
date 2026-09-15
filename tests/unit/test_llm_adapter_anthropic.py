"""Anthropic LLM adapter contract, tool, parameter, and error tests."""

from __future__ import annotations

from types import SimpleNamespace

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
from llm_fakes import (
    _AnthropicClient,
    _card,
    _provider_config,
    _request_response,
    _tool,
)

import fraudlens_llm.adapters.anthropic as anthropic_module
from fraudlens_llm import (
    CapabilityMismatchError,
    DataClass,
    GenerationParams,
    Kind,
    LlmMessage,
    MissingApiKeyError,
    Protocol,
    ProviderConfig,
    Role,
    ToolCall,
    UnsupportedParameterError,
)
from fraudlens_llm.adapters.anthropic import AnthropicAdapter
from fraudlens_llm.exceptions import (
    LlmError,
    LlmRateLimitError,
    LlmTimeoutError,
    ProviderAuthError,
    ProviderError,
)


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

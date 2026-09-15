"""Unit tests for private provider adapters with mocked SDK boundaries."""

from __future__ import annotations

from types import SimpleNamespace

import httpx

from fraudlens_llm import (
    DataClass,
    GenerationParams,
    Kind,
    ModelCard,
    Protocol,
    ProviderConfig,
    ToolDefinition,
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

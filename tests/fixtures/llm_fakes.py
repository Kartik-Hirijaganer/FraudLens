"""Shared LLM test doubles: mocked SDK boundaries and the recording client adapter.

`FakeAdapter` is one fake, not two: the governance and guardrail suites both drive the same
recording adapter, and a second copy would let a capability added to one (a streamed
`response_schema`, say) silently skip the other.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace
from typing import Any

import httpx

from fraudlens_llm import (
    DataClass,
    GenerationParams,
    Kind,
    LlmMessage,
    LlmUsage,
    ModelCard,
    Protocol,
    ProviderConfig,
    ToolCall,
    ToolDefinition,
)
from fraudlens_llm.adapters.base import (
    AdapterEmbeddingResult,
    AdapterGenerateChunk,
    AdapterGenerateResult,
)
from fraudlens_llm.exceptions import LlmTimeoutError


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


class FakeAdapter:
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
        self.stream_response_schemas: list[dict[str, Any] | None] = []
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
        response_schema: dict[str, Any] | None = None,
    ) -> AsyncIterator[AdapterGenerateChunk]:
        _ = (model_id, card)
        self.stream_generate_calls.append(messages)
        self.params.append(params)
        self.stream_response_schemas.append(response_schema)
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

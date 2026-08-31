"""Summary: Private OpenAI-compatible SDK adapter for chat completions, native token
streams, and embeddings. It lazily reads API keys from environment variables,
validates requested parameters against supported capability sets, and normalizes SDK
results and exceptions into FraudLens LLM types.

Key classes:
- OpenAiCompatibleAdapter: Adapter for OpenAI-compatible providers.

Key functions:
- (none)

Notes:
- This adapter is private; public calls go through LlmClient guardrails first.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    OpenAIError,
    RateLimitError,
)

from fraudlens_llm.adapters.base import (
    AdapterEmbeddingResult,
    AdapterGenerateChunk,
    AdapterGenerateResult,
    params_to_dict,
)
from fraudlens_llm.adapters.errors import map_provider_error
from fraudlens_llm.catalog import GenerationParams, Kind, ModelCard
from fraudlens_llm.exceptions import (
    CapabilityMismatchError,
    LlmError,
    MissingApiKeyError,
    ProviderError,
    UnsupportedParameterError,
)
from fraudlens_llm.models import LlmMessage, LlmUsage, ToolCall, ToolDefinition
from fraudlens_llm.providers import ProviderConfig

_CHAT_PARAMS = {
    "temperature",
    "max_tokens",
    "top_p",
    "stop",
    "response_format",
    "seed",
    "frequency_penalty",
    "presence_penalty",
    "reasoning_effort",
}
_EMBED_PARAMS = {"dimensions"}
_RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


class OpenAiCompatibleAdapter:
    """Adapter for OpenAI-compatible providers."""

    def __init__(self, provider: str, config: ProviderConfig) -> None:
        """Create an adapter for one configured provider."""
        self._provider = provider
        self._config = config
        self._client: AsyncOpenAI | None = None

    async def generate(  # noqa: PLR0913 - explicit provider capability arguments.
        self,
        *,
        model_id: str,
        card: ModelCard,
        messages: Sequence[LlmMessage],
        params: GenerationParams,
        tools: Sequence[ToolDefinition] = (),
        tool_choice: str | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> AdapterGenerateResult:
        """Generate chat text through an OpenAI-compatible SDK."""
        if card.kind != Kind.CHAT:
            raise CapabilityMismatchError(f"Model '{model_id}' is not a chat model")
        provider_params = _validated_params(params, _CHAT_PARAMS)
        if "response_format" in provider_params:
            provider_params["response_format"] = {"type": provider_params["response_format"]}
        if response_schema is not None:
            provider_params["response_format"] = _structured_response_format(response_schema)
        if tools:
            provider_params["tools"] = [_openai_tool_definition(tool) for tool in tools]
        if tool_choice is not None:
            provider_params["tool_choice"] = _openai_tool_choice(tool_choice)
        try:
            response = await self._client_instance().chat.completions.create(
                model=model_id,
                messages=cast(Any, [_openai_message(message) for message in messages]),
                **provider_params,
            )
        except OpenAIError as exc:
            raise _map_openai_error(exc, self._provider) from exc
        choice = response.choices[0]
        message = getattr(choice, "message", None)
        text = _content_to_text(getattr(message, "content", ""))
        return AdapterGenerateResult(
            text=text,
            served_model=getattr(response, "model", None),
            finish_reason=getattr(choice, "finish_reason", None),
            usage=_usage_from_openai(response),
            tool_calls=_tool_calls_from_openai(getattr(message, "tool_calls", None)),
        )

    async def embed(
        self,
        *,
        model_id: str,
        card: ModelCard,
        inputs: Sequence[str],
        params: GenerationParams,
    ) -> AdapterEmbeddingResult:
        """Generate embeddings through an OpenAI-compatible SDK."""
        if card.kind != Kind.EMBED:
            raise CapabilityMismatchError(f"Model '{model_id}' is not an embedding model")
        provider_params = _validated_params(params, _EMBED_PARAMS)
        try:
            response = await self._client_instance().embeddings.create(
                model=model_id,
                input=list(inputs),
                **provider_params,
            )
        except OpenAIError as exc:
            raise _map_openai_error(exc, self._provider) from exc
        return AdapterEmbeddingResult(
            embeddings=[list(item.embedding) for item in response.data],
            usage=_usage_from_openai(response),
        )

    async def generate_stream(
        self,
        *,
        model_id: str,
        card: ModelCard,
        messages: Sequence[LlmMessage],
        params: GenerationParams,
    ) -> AsyncIterator[AdapterGenerateChunk]:
        """Yield native chat deltas through an OpenAI-compatible SDK stream."""
        if card.kind != Kind.CHAT:
            raise CapabilityMismatchError(f"Model '{model_id}' is not a chat model")
        provider_params = _validated_params(params, _CHAT_PARAMS)
        if "response_format" in provider_params:
            provider_params["response_format"] = {"type": provider_params["response_format"]}
        try:
            stream = cast(
                Any,
                await self._client_instance().chat.completions.create(
                    model=model_id,
                    messages=cast(Any, [_openai_message(message) for message in messages]),
                    stream=True,
                    stream_options={"include_usage": True},
                    **provider_params,
                ),
            )
            async for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                choice = choices[0] if choices else None
                finish_reason = getattr(choice, "finish_reason", None)
                if getattr(chunk, "error", None) is not None or finish_reason == "error":
                    raise ProviderError(
                        "OpenAI-compatible provider stream interrupted",
                        retryable=True,
                    )
                delta = getattr(choice, "delta", None)
                text_delta = _content_to_text(getattr(delta, "content", ""))
                usage = (
                    _usage_from_openai(chunk) if getattr(chunk, "usage", None) is not None else None
                )
                served_model = getattr(chunk, "model", None)
                if text_delta or served_model or finish_reason or usage is not None:
                    yield AdapterGenerateChunk(
                        text_delta=text_delta,
                        served_model=served_model,
                        finish_reason=finish_reason,
                        usage=usage,
                    )
        except OpenAIError as exc:
            raise _map_openai_error(exc, self._provider) from exc

    def _client_instance(self) -> AsyncOpenAI:
        """Return a lazily constructed SDK client after reading the env-var key."""
        if self._client is not None:
            return self._client
        api_key = os.getenv(self._config.api_key_env)
        if not api_key:
            raise MissingApiKeyError(
                f"Provider '{self._provider}' requires env var {self._config.api_key_env}"
            )
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=self._config.base_url,
            timeout=self._config.timeout_s,
            max_retries=self._config.max_retries,
            default_headers=self._config.headers or None,
        )
        return self._client


def _validated_params(params: GenerationParams, supported: set[str]) -> dict[str, Any]:
    """Return params as a dict after rejecting unsupported keys."""
    provider_params = params_to_dict(params)
    unsupported = sorted(set(provider_params) - supported)
    if unsupported:
        raise UnsupportedParameterError(f"Unsupported OpenAI-compatible params: {unsupported}")
    return provider_params


def _openai_message(message: LlmMessage) -> dict[str, Any]:
    """Map one neutral message without changing ordinary text-only payloads."""
    payload: dict[str, Any] = {"role": message.role.value}
    if message.content is not None:
        payload["content"] = message.content
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.name,
                    "arguments": json.dumps(
                        tool_call.arguments,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            }
            for tool_call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    return payload


def _openai_tool_definition(tool: ToolDefinition) -> dict[str, Any]:
    """Map a provider-neutral tool definition to the OpenAI function shape."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _openai_tool_choice(tool_choice: str) -> str | dict[str, object]:
    """Map a built-in policy or declared tool name to OpenAI tool_choice."""
    if tool_choice in {"auto", "none", "required"}:
        return tool_choice
    return {"type": "function", "function": {"name": tool_choice}}


def _structured_response_format(response_schema: dict[str, Any]) -> dict[str, object]:
    """Build the OpenAI strict JSON-Schema response-format object."""
    title = response_schema.get("title")
    name = title if isinstance(title, str) and title else "structured_response"
    normalized_name = "".join(character if character.isalnum() else "_" for character in name)
    if not normalized_name.strip("_"):
        normalized_name = "structured_response"
    return {
        "type": "json_schema",
        "json_schema": {
            "name": normalized_name[:64],
            "strict": True,
            "schema": _strict_json_schema(response_schema),
        },
    }


def _strict_json_schema(value: Any) -> Any:
    """Copy a JSON Schema into the strict object subset required by OpenAI-compatible APIs."""
    if isinstance(value, list):
        return [_strict_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {key: _strict_json_schema(item) for key, item in value.items() if key != "default"}
    properties = normalized.get("properties")
    if isinstance(properties, dict):
        normalized["additionalProperties"] = False
        normalized["required"] = list(properties)
    return normalized


def _tool_calls_from_openai(raw_tool_calls: Any) -> tuple[ToolCall, ...]:
    """Parse OpenAI function tool calls into provider-neutral models."""
    if not raw_tool_calls:
        return ()
    parsed: list[ToolCall] = []
    for raw_tool_call in raw_tool_calls:
        function = _item_value(raw_tool_call, "function")
        raw_arguments = _item_value(function, "arguments")
        try:
            arguments = (
                json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            )
        except json.JSONDecodeError as exc:
            raise ProviderError(
                "OpenAI-compatible provider returned malformed tool arguments"
            ) from exc
        if not isinstance(arguments, dict):
            raise ProviderError("OpenAI-compatible provider returned non-object tool arguments")
        tool_call_id = _item_value(raw_tool_call, "id")
        name = _item_value(function, "name")
        if not isinstance(tool_call_id, str) or not isinstance(name, str):
            raise ProviderError("OpenAI-compatible provider returned an invalid tool call")
        parsed.append(ToolCall(id=tool_call_id, name=name, arguments=arguments))
    return tuple(parsed)


def _item_value(item: Any, key: str) -> Any:
    """Read one field from an SDK model or a test dictionary."""
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _content_to_text(content: Any) -> str:
    """Normalize SDK message content into text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                value = item.get("text")
                if isinstance(value, str):
                    parts.append(value)
            else:
                value = getattr(item, "text", None)
                if isinstance(value, str):
                    parts.append(value)
        return "".join(parts)
    return "" if content is None else str(content)


def _usage_from_openai(response: Any) -> LlmUsage:
    """Normalize token usage fields from OpenAI-compatible responses."""
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", 0)) or 0)
    output_tokens = int(
        getattr(usage, "completion_tokens", getattr(usage, "output_tokens", 0)) or 0
    )
    total_tokens = int(getattr(usage, "total_tokens", input_tokens + output_tokens) or 0)
    return LlmUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _map_openai_error(exc: OpenAIError, provider: str) -> LlmError:
    """Map SDK exceptions into library exceptions with retryability metadata."""
    return map_provider_error(
        exc,
        provider,
        timeout_error=APITimeoutError,
        rate_limit_error=RateLimitError,
        auth_error=AuthenticationError,
        connection_error=APIConnectionError,
        bad_request_error=BadRequestError,
        status_error=APIStatusError,
        retryable_status_codes=_RETRYABLE_STATUS_CODES,
    )

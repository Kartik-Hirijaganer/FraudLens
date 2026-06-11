"""Summary: Private Anthropic SDK adapter for chat generation. It lazily reads API
keys, validates supported parameters, normalizes messages/results, and rejects
embeddings because Anthropic has no native embedding API in v1.

Key classes:
- AnthropicAdapter: Adapter for Anthropic chat models.

Key functions:
- (none)

Notes:
- This adapter is private; public calls go through LlmClient guardrails first.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any, cast

from anthropic import (
    AnthropicError,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from fraudlens_llm.adapters import base as adapter_base
from fraudlens_llm.adapters.errors import map_provider_error
from fraudlens_llm.catalog import GenerationParams, Kind, ModelCard
from fraudlens_llm.exceptions import (
    CapabilityMismatchError,
    LlmError,
    MissingApiKeyError,
    UnsupportedParameterError,
)
from fraudlens_llm.models import LlmMessage, LlmUsage, Role
from fraudlens_llm.providers import ProviderConfig

_CHAT_PARAMS = {"temperature", "max_tokens", "top_p", "stop"}
_RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


class AnthropicAdapter:
    """Adapter for Anthropic chat models."""

    def __init__(self, provider: str, config: ProviderConfig) -> None:
        """Create an adapter for one configured Anthropic provider."""
        self._provider = provider
        self._config = config
        self._client: AsyncAnthropic | None = None

    async def generate(
        self,
        *,
        model_id: str,
        card: ModelCard,
        messages: Sequence[LlmMessage],
        params: GenerationParams,
    ) -> adapter_base.AdapterGenerateResult:
        """Generate chat text through the Anthropic SDK."""
        if card.kind != Kind.CHAT:
            raise CapabilityMismatchError(f"Model '{model_id}' is not a chat model")
        provider_params = _validated_params(params, _CHAT_PARAMS)
        if "stop" in provider_params:
            provider_params["stop_sequences"] = provider_params.pop("stop")
        system_text, anthropic_messages = _anthropic_messages(messages)
        request_kwargs: dict[str, Any] = {
            "model": model_id,
            "messages": cast(Any, anthropic_messages),
            **provider_params,
        }
        if system_text:
            request_kwargs["system"] = system_text
        try:
            response = await self._client_instance().messages.create(**request_kwargs)
        except AnthropicError as exc:
            raise _map_anthropic_error(exc, self._provider) from exc
        return adapter_base.AdapterGenerateResult(
            text=_content_to_text(response.content),
            served_model=getattr(response, "model", None),
            finish_reason=getattr(response, "stop_reason", None),
            usage=_usage_from_anthropic(response),
        )

    async def embed(
        self,
        *,
        model_id: str,
        card: ModelCard,
        inputs: Sequence[str],
        params: GenerationParams,
    ) -> adapter_base.AdapterEmbeddingResult:
        """Reject Anthropic embeddings because they are unsupported in v1."""
        _ = (model_id, card, inputs, params)
        raise CapabilityMismatchError("Anthropic does not support native embeddings in v1")

    def _client_instance(self) -> AsyncAnthropic:
        """Return a lazily constructed SDK client after reading the env-var key."""
        if self._client is not None:
            return self._client
        api_key = os.getenv(self._config.api_key_env)
        if not api_key:
            raise MissingApiKeyError(
                f"Provider '{self._provider}' requires env var {self._config.api_key_env}"
            )
        if self._config.base_url is None:
            self._client = AsyncAnthropic(
                api_key=api_key,
                timeout=self._config.timeout_s,
                max_retries=self._config.max_retries,
            )
        else:
            self._client = AsyncAnthropic(
                api_key=api_key,
                base_url=self._config.base_url,
                timeout=self._config.timeout_s,
                max_retries=self._config.max_retries,
            )
        return self._client


def _validated_params(params: GenerationParams, supported: set[str]) -> dict[str, Any]:
    """Return params as a dict after rejecting unsupported keys."""
    provider_params = adapter_base.params_to_dict(params)
    unsupported = sorted(set(provider_params) - supported)
    if unsupported:
        raise UnsupportedParameterError(f"Unsupported Anthropic params: {unsupported}")
    return provider_params


def _anthropic_messages(messages: Sequence[LlmMessage]) -> tuple[str, list[dict[str, str]]]:
    """Split system messages from Anthropic user/assistant messages."""
    system_parts: list[str] = []
    anthropic_messages: list[dict[str, str]] = []
    for message in messages:
        if message.role == Role.SYSTEM:
            system_parts.append(message.content)
        else:
            anthropic_messages.append({"role": message.role.value, "content": message.content})
    return "\n\n".join(system_parts), anthropic_messages


def _content_to_text(content: Any) -> str:
    """Normalize Anthropic content blocks into text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            value = getattr(item, "text", None)
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return "" if content is None else str(content)


def _usage_from_anthropic(response: Any) -> LlmUsage:
    """Normalize token usage fields from Anthropic responses."""
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return LlmUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


def _map_anthropic_error(exc: AnthropicError, provider: str) -> LlmError:
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

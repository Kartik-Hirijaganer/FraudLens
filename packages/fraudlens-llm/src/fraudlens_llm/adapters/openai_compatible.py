"""Summary: Private OpenAI-compatible SDK adapter for chat completions and
embeddings. It lazily reads API keys from environment variables, validates
requested parameters against supported capability sets, and normalizes SDK results
and exceptions into FraudLens LLM types.

Key classes:
- OpenAiCompatibleAdapter: Adapter for OpenAI-compatible providers.

Key functions:
- (none)

Notes:
- This adapter is private; public calls go through LlmClient guardrails first.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
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
    AdapterGenerateResult,
    params_to_dict,
)
from fraudlens_llm.adapters.errors import map_provider_error
from fraudlens_llm.catalog import GenerationParams, Kind, ModelCard
from fraudlens_llm.exceptions import (
    CapabilityMismatchError,
    LlmError,
    MissingApiKeyError,
    UnsupportedParameterError,
)
from fraudlens_llm.models import LlmMessage, LlmUsage
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

    async def generate(
        self,
        *,
        model_id: str,
        card: ModelCard,
        messages: Sequence[LlmMessage],
        params: GenerationParams,
    ) -> AdapterGenerateResult:
        """Generate chat text through an OpenAI-compatible SDK."""
        if card.kind != Kind.CHAT:
            raise CapabilityMismatchError(f"Model '{model_id}' is not a chat model")
        provider_params = _validated_params(params, _CHAT_PARAMS)
        if "response_format" in provider_params:
            provider_params["response_format"] = {"type": provider_params["response_format"]}
        try:
            response = await self._client_instance().chat.completions.create(
                model=model_id,
                messages=cast(
                    Any,
                    [message.model_dump(mode="json") for message in messages],
                ),
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

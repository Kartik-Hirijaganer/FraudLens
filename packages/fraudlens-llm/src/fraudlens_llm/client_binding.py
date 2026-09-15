"""Summary: Public bound-model and streaming request contracts for the LLM client.

Key classes:
- StreamGenerationRequest: typed native-stream request.
- BoundModel: convenience wrapper bound to one provider/model reference.

Key functions:
- collect_adapter_stream: normalize a provider-native stream.

Notes:
- Raw stream deltas remain internal until complete-output guardrails pass.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from fraudlens_llm.adapters.base import (
    AdapterGenerateResult,
    StreamingProviderAdapter,
)
from fraudlens_llm.catalog import GenerationParams, ModelCard
from fraudlens_llm.models import (
    DataClass,
    EmbeddingResult,
    GuardrailDecision,
    LlmMessage,
    LlmResult,
    LlmUsage,
    ScanOutcome,
    TaskType,
    ToolDefinition,
)

_LOGGER = logging.getLogger(__name__)
_MESSAGE_ADAPTER: TypeAdapter[list[LlmMessage]] = TypeAdapter(list[LlmMessage])
_TOKENS_PER_MILLION = 1_000_000
_NOT_APPLICABLE = ScanOutcome(decision=GuardrailDecision.NOT_APPLICABLE, findings=[])


if TYPE_CHECKING:
    from fraudlens_llm.client import LlmClient


class StreamGenerationRequest(BaseModel):
    """Typed request for a provider-native stream assembled behind output guardrails."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    messages: Sequence[LlmMessage] = Field(
        ..., description="Chat messages validated and masked before the provider call."
    )
    model: str | None = Field(default=None, description="Provider/model reference override.")
    overrides: GenerationParams | None = Field(
        default=None, description="Per-call generation parameter overrides."
    )
    task_type: TaskType = Field(
        default=TaskType.GENERATION, description="Guardrail task classification."
    )
    data_class: DataClass | None = Field(
        default=None, description="Provider-governance data classification override."
    )
    include_raw: bool = Field(
        default=False, description="Whether non-production policy may return raw output."
    )
    fallbacks: tuple[str, ...] = Field(
        default=(), description="Ordered governance-eligible fallback model references."
    )


class BoundModel:
    """Convenience wrapper bound to one provider/model reference."""

    def __init__(self, client: LlmClient, ref: str) -> None:
        """Create a bound model wrapper."""
        self._client = client
        self._ref = ref

    async def generate(  # noqa: PLR0913 - mirrors LlmClient.generate for bound models.
        self,
        messages: Sequence[LlmMessage | dict[str, object]],
        *,
        overrides: GenerationParams | None = None,
        task_type: TaskType = TaskType.GENERATION,
        data_class: DataClass | None = None,
        include_raw: bool = False,
        fallbacks: Sequence[str] | None = None,
        tools: Sequence[ToolDefinition] | None = None,
        tool_choice: str | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> LlmResult:
        """Generate text with this bound model."""
        return await self._client.generate(
            messages,
            model=self._ref,
            overrides=overrides,
            task_type=task_type,
            data_class=data_class,
            include_raw=include_raw,
            fallbacks=fallbacks,
            tools=tools,
            tool_choice=tool_choice,
            response_schema=response_schema,
        )

    async def embed(
        self,
        inputs: Sequence[str],
        *,
        overrides: GenerationParams | None = None,
        data_class: DataClass | None = None,
    ) -> EmbeddingResult:
        """Generate embeddings with this bound model."""
        return await self._client.embed(
            inputs,
            model=self._ref,
            overrides=overrides,
            data_class=data_class,
        )


async def collect_adapter_stream(
    adapter: StreamingProviderAdapter,
    *,
    model_id: str,
    card: ModelCard,
    messages: Sequence[LlmMessage],
    params: GenerationParams,
) -> AdapterGenerateResult:
    """Assemble native provider deltas into the normalized result guardrails consume."""
    text_parts: list[str] = []
    served_model: str | None = None
    finish_reason: str | None = None
    usage = LlmUsage()
    async for chunk in adapter.generate_stream(
        model_id=model_id,
        card=card,
        messages=messages,
        params=params,
    ):
        text_parts.append(chunk.text_delta)
        served_model = chunk.served_model or served_model
        finish_reason = chunk.finish_reason or finish_reason
        usage = chunk.usage or usage
    return AdapterGenerateResult(
        text="".join(text_parts),
        served_model=served_model,
        finish_reason=finish_reason,
        usage=usage,
    )

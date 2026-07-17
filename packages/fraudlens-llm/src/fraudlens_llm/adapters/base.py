"""Summary: Private adapter protocol and normalized provider result models. These
types isolate provider SDK details from the public client and keep all public
provider calls behind the guardrailed client pipeline.

Key classes:
- AdapterGenerateResult: Normalized chat result returned by adapters.
- AdapterGenerateChunk: One normalized provider-native chat stream chunk.
- AdapterEmbeddingResult: Normalized embedding result returned by adapters.
- ProviderAdapter: Private protocol implemented by SDK adapters.
- StreamingProviderAdapter: Private protocol for native streaming adapters.

Key functions:
- params_to_dict: Convert GenerationParams into a provider parameter dict.

Notes:
- This module is intentionally not exported from fraudlens_llm.__all__.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_llm.catalog import GenerationParams, ModelCard
from fraudlens_llm.models import LlmMessage, LlmUsage


class AdapterGenerateResult(BaseModel):
    """Normalized chat result returned by provider adapters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(..., description="Raw provider text.")
    served_model: str | None = Field(default=None, description="Provider-reported model.")
    finish_reason: str | None = Field(default=None, description="Provider finish reason.")
    usage: LlmUsage = Field(..., description="Normalized usage metrics.")


class AdapterGenerateChunk(BaseModel):
    """One normalized provider-native chat stream chunk."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text_delta: str = Field(default="", description="Incremental raw provider text.")
    served_model: str | None = Field(default=None, description="Provider-reported model.")
    finish_reason: str | None = Field(default=None, description="Provider finish reason.")
    usage: LlmUsage | None = Field(
        default=None, description="Normalized usage metrics, normally present on the final chunk."
    )


class AdapterEmbeddingResult(BaseModel):
    """Normalized embedding result returned by provider adapters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    embeddings: list[list[float]] = Field(..., description="Embedding vectors.")
    usage: LlmUsage = Field(..., description="Normalized usage metrics.")


class ProviderAdapter(Protocol):
    """Private provider adapter protocol."""

    async def generate(
        self,
        *,
        model_id: str,
        card: ModelCard,
        messages: Sequence[LlmMessage],
        params: GenerationParams,
    ) -> AdapterGenerateResult:
        """Generate text with a provider SDK."""

    async def embed(
        self,
        *,
        model_id: str,
        card: ModelCard,
        inputs: Sequence[str],
        params: GenerationParams,
    ) -> AdapterEmbeddingResult:
        """Generate embeddings with a provider SDK."""


class StreamingProviderAdapter(ProviderAdapter, Protocol):
    """Private provider adapter protocol for native chat streaming."""

    def generate_stream(
        self,
        *,
        model_id: str,
        card: ModelCard,
        messages: Sequence[LlmMessage],
        params: GenerationParams,
    ) -> AsyncIterator[AdapterGenerateChunk]:
        """Yield normalized provider-native text deltas and terminal metadata."""


def params_to_dict(params: GenerationParams) -> dict[str, Any]:
    """Convert GenerationParams into a provider parameter dict."""
    return params.model_dump(exclude_none=True)

"""Summary: Shared offline/live RAG embedder factory. Both pipeline retrieval and the ingest
script call this module, preventing indexes from being built and queried with independently chosen
embedding spaces. Live configuration is validated from `config/llm/rag.yml`; secrets remain in
Infisical and are resolved lazily by `LlmClient` through the OpenRouter provider.

Key classes:
- RagEmbeddingConfig: non-secret live embedding model, dimensions, version, and data class.

Key functions:
- load_rag_embedding_config: load and validate `config/llm/rag.yml`.
- build_embedder: select deterministic hashing or the live LLM-client adapter from settings.

Notes:
- Offline mode does not load provider config or require credentials.
- The model id and dimensions live in YAML, never source.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from fraudlens_backend.rag.embedder import LlmClientEmbedder
from fraudlens_backend.settings import AppSettings, find_config_dir
from fraudlens_llm import DataClass, LlmClient
from fraudlens_ml.rag import Embedder, HashingEmbedder


class RagEmbeddingConfig(BaseModel):
    """Non-secret live embedding-space selection loaded from config/llm/rag.yml."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    model: str = Field(..., min_length=1, description="Embedding catalog reference.")
    dimensions: int = Field(..., gt=0, description="Expected provider vector dimensions.")
    rag_version: str = Field(
        ..., min_length=1, description="Audit version encoding corpus and embedding space."
    )
    data_class: DataClass = Field(
        ..., description="Provider-governance class for PHI-free regulatory embedding inputs."
    )


def load_rag_embedding_config(path: Path | None = None) -> RagEmbeddingConfig:
    """Load and validate the live RAG embedding selection from config/llm/rag.yml."""
    config_path = path or (find_config_dir() / "llm" / "rag.yml")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return RagEmbeddingConfig.model_validate(raw)


def build_embedder(
    settings: AppSettings,
    *,
    client: LlmClient | None = None,
    config: RagEmbeddingConfig | None = None,
) -> Embedder:
    """Build the offline or live embedder selected by `settings.rag_embedding_mode`."""
    if settings.rag_embedding_mode == "offline":
        return HashingEmbedder(rag_version=settings.rag_version)
    resolved = config or load_rag_embedding_config()
    return LlmClientEmbedder(
        client=client or LlmClient.from_settings(),
        model=resolved.model,
        dimensions=resolved.dimensions,
        rag_version=resolved.rag_version,
        data_class=resolved.data_class,
    )

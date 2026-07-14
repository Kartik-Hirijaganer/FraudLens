"""Backend adapters and configuration for offline/live regulatory RAG embeddings."""

from fraudlens_backend.rag.embedder import LlmClientEmbedder
from fraudlens_backend.rag.factory import (
    RagEmbeddingConfig,
    build_embedder,
    load_rag_embedding_config,
)

__all__ = [
    "LlmClientEmbedder",
    "RagEmbeddingConfig",
    "build_embedder",
    "load_rag_embedding_config",
]

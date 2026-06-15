"""fraudlens-ml RAG over FinCEN/BSA (plan §16 Phase 6): deterministic corpus chunking, an
offline/zero-key embedder + live-embedder seam, ChromaDB persistence, a retriever with a
lexical embeddings-down fallback, and the RAG-as-data citation/injection defense. Re-exports
are intentional (the public RAG surface). Layering: imports fraudlens-core only — never
fraudlens-backend or fraudlens-llm (the embedder is an injected protocol)."""

from __future__ import annotations

from fraudlens_ml.rag.citations import (
    DEFAULT_SNIPPET_CHARS,
    Citation,
    build_rag_context,
    escape_as_data,
    extract_citations,
)
from fraudlens_ml.rag.ingest import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    Chunk,
    Embedder,
    HashingEmbedder,
    RegulationDocument,
    build_index,
    chunk_corpus,
    chunk_document,
    connect,
    load_corpus,
    tokenize,
)
from fraudlens_ml.rag.retriever import (
    DEFAULT_RAG_VERSION,
    DEFAULT_TOP_K,
    IndexStatus,
    RetrievalMode,
    RetrievalResult,
    RetrievedChunk,
    Retriever,
    index_status,
)

__all__ = [
    "DEFAULT_CHUNK_OVERLAP",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_RAG_VERSION",
    "DEFAULT_SNIPPET_CHARS",
    "DEFAULT_TOP_K",
    "Chunk",
    "Citation",
    "Embedder",
    "HashingEmbedder",
    "IndexStatus",
    "RegulationDocument",
    "RetrievalMode",
    "RetrievalResult",
    "RetrievedChunk",
    "Retriever",
    "build_index",
    "build_rag_context",
    "chunk_corpus",
    "chunk_document",
    "connect",
    "escape_as_data",
    "extract_citations",
    "index_status",
    "load_corpus",
    "tokenize",
]

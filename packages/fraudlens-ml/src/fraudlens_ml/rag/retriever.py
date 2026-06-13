"""Summary: The FinCEN/BSA RAG retriever — the read side of the index `ingest.py` builds
(plan §16 Phase 6). `Retriever.retrieve` resolves the persisted ChromaDB collection, embeds the
query, and returns the top-k most relevant regulatory chunks with their citations. It is built
for graceful degradation around a deterministic core (plan §10.6): if the query embedder fails
(a down/keyless embeddings provider) it transparently falls back to a deterministic LEXICAL
ranking over the same baked chunks; if the index is missing or empty it returns an empty result
flagged as such — so an investigation always continues with citations, lexical citations, or a
clean "no citations" signal, never a hard failure. `index_status` is the cheap presence check
`/readyz` uses to gate a replica on the baked index (plan §16 Phase 6 API change). Retrieval
touches only the local index — no DB, no network in the offline path.

Key classes:
- RetrievedChunk: one retrieved chunk — its text, citation metadata, and relevance score.
- RetrievalResult: the ordered chunks plus the retrieval `mode` flag and the rag version.
- Retriever: retrieves cited chunks for a query (vector search, lexical fallback, else empty).

Key functions:
- index_status: classify the persisted index as 'ready', 'empty', or 'missing' (readiness).

Notes:
- `mode` is the degradation flag: 'vector' (embedding search), 'lexical' (embeddings-down
fallback), or 'empty' (missing/empty index → []). The pipeline records it on the run.
- The lexical fallback and the embedder share `ingest.tokenize`, so ranking and embedding agree
on what a token is (no duplicated tokenizer).
- `index_status` checks the on-disk sqlite marker BEFORE opening a client, so a readiness probe
on an un-built index never creates an empty index directory as a side effect.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
from chromadb import Collection
from chromadb.api.types import Embeddings, QueryResult
from pydantic import BaseModel, ConfigDict, Field

from fraudlens_ml.rag.ingest import Embedder, connect, tokenize

DEFAULT_TOP_K = 4
DEFAULT_RAG_VERSION = "rag-v1"
_SQLITE_MARKER = "chroma.sqlite3"

IndexStatus = Literal["ready", "empty", "missing"]
RetrievalMode = Literal["vector", "lexical", "empty"]


class RetrievedChunk(BaseModel):
    """One retrieved regulatory chunk: its text, citation metadata, and relevance score."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: str = Field(..., description="Stable id of the chunk ('<docId>::<index>').")
    doc_id: str = Field(..., description="Id of the source regulatory document.")
    citation: str = Field(..., description="Exact regulatory citation for the chunk.")
    title: str = Field(..., description="Title of the source provision.")
    source: str = Field(..., description="Publisher of the provision.")
    text: str = Field(..., description="The retrieved chunk text (reference material, no PHI).")
    score: float = Field(
        ..., description="Relevance: cosine similarity (vector) or token overlap (lexical)."
    )


class RetrievalResult(BaseModel):
    """The ordered retrieved chunks plus the degradation-mode flag and the rag version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunks: list[RetrievedChunk] = Field(
        default_factory=list, description="Top-k chunks, most relevant first (possibly empty)."
    )
    mode: RetrievalMode = Field(
        ..., description="'vector', 'lexical' (embeddings-down), or 'empty' (no index)."
    )
    rag_version: str = Field(..., description="Version of the corpus/index used for retrieval.")


def index_status(persist_dir: Path, collection: str) -> IndexStatus:
    """Classify the persisted index as 'ready' (has chunks), 'empty', or 'missing'."""
    if not (persist_dir / _SQLITE_MARKER).is_file():
        return "missing"
    try:
        store = connect(persist_dir).get_collection(collection)
        return "ready" if store.count() > 0 else "empty"
    except Exception:  # any open/lookup failure → treat as missing (readiness never raises)
        return "missing"


def _meta_str(metadata: Mapping[str, Any], key: str) -> str:
    """Read a string field from chunk metadata, defaulting to '' (PHI-free, never None)."""
    value = metadata.get(key)
    return value if isinstance(value, str) else ""


def _chunk_from_meta(
    chunk_id: str, text: str, metadata: Mapping[str, Any], score: float
) -> RetrievedChunk:
    """Build a RetrievedChunk from a stored id/document/metadata triple and a score."""
    return RetrievedChunk(
        chunk_id=chunk_id,
        doc_id=_meta_str(metadata, "doc_id"),
        citation=_meta_str(metadata, "citation"),
        title=_meta_str(metadata, "title"),
        source=_meta_str(metadata, "source"),
        text=text,
        score=score,
    )


class Retriever:
    """Retrieves cited FinCEN/BSA chunks for a query, with a lexical embeddings-down fallback."""

    def __init__(
        self,
        *,
        persist_dir: Path,
        collection: str,
        embedder: Embedder,
        rag_version: str = DEFAULT_RAG_VERSION,
    ) -> None:
        """Bind the index location, collection, query embedder, and corpus version."""
        self._persist_dir = persist_dir
        self._collection = collection
        self._embedder = embedder
        self._rag_version = rag_version

    def _empty(self, mode: RetrievalMode) -> RetrievalResult:
        """Return an empty result carrying the given degradation mode + rag version."""
        return RetrievalResult(chunks=[], mode=mode, rag_version=self._rag_version)

    def retrieve(self, query: str, *, top_k: int = DEFAULT_TOP_K) -> RetrievalResult:
        """Return the top-k relevant chunks: vector search, else lexical, else empty."""
        if index_status(self._persist_dir, self._collection) != "ready":
            return self._empty("empty")
        store = connect(self._persist_dir).get_collection(self._collection)
        try:
            embedding = np.asarray(self._embedder.embed_query(query), dtype=np.float32)
        except Exception:  # embeddings provider down → deterministic lexical fallback (§10.6)
            return self._lexical(store, query, top_k)
        result = store.query(
            query_embeddings=cast(Embeddings, [embedding]),
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        return self._from_vector(result)

    def _from_vector(self, result: QueryResult) -> RetrievalResult:
        """Convert a ChromaDB query result into a vector-mode RetrievalResult."""
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        chunks = [
            _chunk_from_meta(ids[i], documents[i], metadatas[i], 1.0 - float(distances[i]))
            for i in range(len(ids))
        ]
        return RetrievalResult(chunks=chunks, mode="vector", rag_version=self._rag_version)

    def _lexical(self, store: Collection, query: str, top_k: int) -> RetrievalResult:
        """Rank all baked chunks by deterministic token overlap (embeddings-down fallback)."""
        data = store.get(include=["documents", "metadatas"])
        query_tokens = set(tokenize(query))
        scored: list[tuple[float, str, str, Mapping[str, Any]]] = []
        for chunk_id, document, metadata in zip(
            data["ids"], data["documents"] or [], data["metadatas"] or [], strict=True
        ):
            doc_tokens = tokenize(document)
            overlap = sum(1 for token in doc_tokens if token in query_tokens)
            score = overlap / len(doc_tokens) if doc_tokens else 0.0
            scored.append((score, chunk_id, document, metadata))
        scored.sort(key=lambda row: (-row[0], row[1]))  # score desc, then id for determinism
        chunks = [
            _chunk_from_meta(chunk_id, document, metadata, score)
            for score, chunk_id, document, metadata in scored[:top_k]
            if score > 0.0
        ]
        return RetrievalResult(chunks=chunks, mode="lexical", rag_version=self._rag_version)

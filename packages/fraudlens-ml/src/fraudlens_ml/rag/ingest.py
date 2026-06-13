"""Summary: The FinCEN/BSA RAG ingestion pipeline — corpus loading, deterministic chunking,
embedding, and ChromaDB persistence (plan §16 Phase 6). It loads the committed regulatory
corpus (`data/regulations/*.md`, each a provision with a small metadata header), splits each
document into deterministic overlapping chunks, embeds the chunks, and writes them into a
persistent ChromaDB collection the retriever reads back. The default `HashingEmbedder` is a
pure-Python, deterministic, dependency-free embedder so `make ingest-rag` and `make local-demo`
build a real vector index with NO API keys, NO network, and NO cost; the same `Embedder`
protocol is the seam a live `text-embedding-3-small` embedder plugs into for production
(selected by config, mirroring the SAR drafter mock/live split — plan §7). Building the index
is the write side; `retriever.py` is the read side and reuses `connect` here so the store's
shape (collection name, cosine space) is defined in exactly one place (no duplication).

Key classes:
- RegulationDocument: one loaded regulatory provision (id, title, citation, source, text).
- Chunk: a deterministic slice of a document plus the citation metadata it carries.
- Embedder: the protocol an embedding backend implements (offline hashing or live provider).
- HashingEmbedder: deterministic, offline, zero-dependency hashed bag-of-tokens embedder.

Key functions:
- connect: build a telemetry-free ChromaDB PersistentClient at a directory (shared read/write).
- tokenize: the package's one lowercase alphanumeric tokenizer (embedder + lexical fallback).
- load_corpus: read every `*.md` provision under a directory into ordered RegulationDocuments.
- chunk_document: split one document into deterministic, overlapping Chunks.
- chunk_corpus: chunk an ordered corpus into a single ordered list of Chunks.
- build_index: (re)build the ChromaDB collection from chunks + an embedder; return the count.

Notes:
- Token hashing uses hashlib (NOT Python's salted built-in hash), so embeddings — and thus the
  whole index — are byte-reproducible across processes and machines (deterministic-chunking +
  top-k-relevance tests, plan §16 Phase 6).
- `build_index` deletes any existing collection first, so re-running ingest is idempotent and a
  shrunk corpus never leaves stale chunks behind.
- The corpus is curated text/markdown (not PDFs): diff-reviewable, deterministic, no PHI, and no
  PDF-parser dependency (plan §16 Phase 6 risk note "curate corpus / parse once").
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

import chromadb
import numpy as np
from chromadb.api import ClientAPI
from chromadb.api.types import Embeddings
from chromadb.config import Settings
from pydantic import BaseModel, ConfigDict, Field

# Default deterministic chunking geometry (chars). Callers/CLI may override; kept as module
# constants — algorithmic shape, not deployment config — mirroring scoring's feature constants.
DEFAULT_CHUNK_SIZE = 900
DEFAULT_CHUNK_OVERLAP = 150
_EMBED_DIMENSIONS = 256
_FRONTMATTER_FENCE = "---"
_REQUIRED_META = ("docId", "title", "citation", "source")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_COSINE_METADATA = {"hnsw:space": "cosine"}


class RegulationDocument(BaseModel):
    """One loaded regulatory provision: stable id, human title, exact citation, source, body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str = Field(..., description="Stable kebab-case id; also the chunk-id prefix.")
    title: str = Field(..., description="Human-readable provision title.")
    citation: str = Field(..., description="Exact regulatory citation surfaced to analysts.")
    source: str = Field(..., description="Publisher of the provision (e.g. FinCEN / BSA).")
    text: str = Field(..., description="The provision body text (no PHI; reference material).")


class Chunk(BaseModel):
    """A deterministic slice of a document carrying the citation metadata it was drawn from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: str = Field(..., description="Stable unique id: '<docId>::<index>'.")
    doc_id: str = Field(..., description="Id of the source document.")
    citation: str = Field(..., description="Citation inherited from the source document.")
    title: str = Field(..., description="Title inherited from the source document.")
    source: str = Field(..., description="Source inherited from the source document.")
    chunk_index: int = Field(..., ge=0, description="0-based position of the chunk in the doc.")
    text: str = Field(..., description="The chunk's text content.")

    def metadata(self) -> dict[str, str | int]:
        """Return the ChromaDB metadata payload (scalar values only) for this chunk."""
        return {
            "doc_id": self.doc_id,
            "citation": self.citation,
            "title": self.title,
            "source": self.source,
            "chunk_index": self.chunk_index,
        }


@runtime_checkable
class Embedder(Protocol):
    """An embedding backend: offline hashing in dev, a live provider on the compliance path."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of chunk texts into vectors (one row per text)."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string into one vector."""
        ...


def tokenize(text: str) -> list[str]:
    """Lowercase + split text into alphanumeric tokens (the RAG package's one tokenizer)."""
    return _TOKEN_RE.findall(text.lower())


class HashingEmbedder:
    """Deterministic, offline, zero-dependency signed hashed bag-of-tokens embedder."""

    def __init__(self, dimensions: int = _EMBED_DIMENSIONS) -> None:
        """Bind the output dimensionality of the hashed embedding space."""
        self._dimensions = dimensions

    def _vector(self, text: str) -> list[float]:
        """Map text to an L2-normalized signed-hashing vector (deterministic via hashlib)."""
        vector = np.zeros(self._dimensions, dtype=np.float64)
        for token in tokenize(text):
            digest = hashlib.sha1(token.encode("utf-8")).digest()  # non-crypto: stable hashing
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        norm = float(np.linalg.norm(vector))
        return (vector / norm).tolist() if norm else vector.tolist()

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each text into a deterministic hashed vector."""
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string into a deterministic hashed vector."""
        return self._vector(text)


def connect(persist_dir: Path) -> ClientAPI:
    """Build a telemetry-free ChromaDB PersistentClient rooted at a directory."""
    persist_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(persist_dir), settings=Settings(anonymized_telemetry=False)
    )


def _parse_frontmatter(text: str, *, where: str) -> tuple[dict[str, str], str]:
    """Split a '--- key: value ---' header from the body; validate required keys."""
    if not text.lstrip().startswith(_FRONTMATTER_FENCE):
        raise ValueError(f"corpus document missing frontmatter header: {where}")
    _, header, body = text.split(_FRONTMATTER_FENCE, 2)
    meta: dict[str, str] = {}
    for line in header.strip().splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    missing = [key for key in _REQUIRED_META if not meta.get(key)]
    if missing:
        raise ValueError(f"corpus document {where} missing metadata keys: {missing}")
    return meta, body.strip()


def load_corpus(directory: Path) -> list[RegulationDocument]:
    """Read every `*.md` provision (skipping README) into ordered RegulationDocuments."""
    documents: list[RegulationDocument] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"), where=path.name)
        documents.append(
            RegulationDocument(
                doc_id=meta["docId"],
                title=meta["title"],
                citation=meta["citation"],
                source=meta["source"],
                text=body,
            )
        )
    return documents


def _next_start(words: list[str], start: int, end: int, overlap: int) -> int:
    """Return the next chunk's start index, retaining ~`overlap` chars and forcing progress."""
    retained = 0
    cursor = end
    while cursor > start + 1 and retained + len(words[cursor - 1]) + 1 <= overlap:
        cursor -= 1
        retained += len(words[cursor]) + 1
    return max(start + 1, cursor)


def chunk_document(
    document: RegulationDocument,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Chunk]:
    """Split one document into deterministic, overlapping word-packed chunks."""
    words = document.text.split()
    if not words:
        return []
    chunks: list[Chunk] = []
    start = 0
    while True:  # _next_start strictly advances `start`; the tail `break` is the sole exit
        end, length = start, 0
        while end < len(words) and length + len(words[end]) + (1 if length else 0) <= chunk_size:
            length += len(words[end]) + (1 if length else 0)
            end += 1
        end = max(end, start + 1)  # never stall on a single over-long word
        chunks.append(
            Chunk(
                chunk_id=f"{document.doc_id}::{len(chunks)}",
                doc_id=document.doc_id,
                citation=document.citation,
                title=document.title,
                source=document.source,
                chunk_index=len(chunks),
                text=" ".join(words[start:end]),
            )
        )
        if end >= len(words):
            break
        start = _next_start(words, start, end, overlap)
    return chunks


def chunk_corpus(
    documents: Sequence[RegulationDocument],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Chunk]:
    """Chunk an ordered corpus into one ordered list of Chunks (document order preserved)."""
    chunks: list[Chunk] = []
    for document in documents:
        chunks.extend(chunk_document(document, chunk_size=chunk_size, overlap=overlap))
    return chunks


def build_index(
    chunks: Sequence[Chunk], *, embedder: Embedder, persist_dir: Path, collection: str
) -> int:
    """(Re)build the ChromaDB collection from chunks + an embedder; return the chunk count."""
    client = connect(persist_dir)
    if collection in {existing.name for existing in client.list_collections()}:
        client.delete_collection(collection)  # idempotent rebuild: no stale chunks survive
    store = client.get_or_create_collection(collection, metadata=dict(_COSINE_METADATA))
    if not chunks:
        return 0
    texts = [chunk.text for chunk in chunks]
    # cast: ChromaDB's Embeddings alias uses an invariant union dtype our float32 rows satisfy.
    embeddings = cast(
        Embeddings, [np.asarray(row, dtype=np.float32) for row in embedder.embed_documents(texts)]
    )
    store.add(
        ids=[chunk.chunk_id for chunk in chunks],
        documents=texts,
        embeddings=embeddings,
        metadatas=[chunk.metadata() for chunk in chunks],
    )
    return int(store.count())

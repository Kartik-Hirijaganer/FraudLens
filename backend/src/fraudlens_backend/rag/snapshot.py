"""Summary: Verified public-corpus embeddings for keyless semantic-index builds.

Key classes:
- CorpusVector: one content-addressed regulatory embedding.
- CorpusSnapshot: model provenance, public vectors, and measured generation usage.
- SnapshotEmbedder: materialize an index from reviewed vectors without provider calls.

Key functions:
- snapshot_path: resolve the configured public embedding artifact.
- refresh_snapshot: explicitly regenerate the public corpus vectors through the live adapter.

Notes:
- Only regulatory document vectors are persisted; investigation queries never enter the artifact.
- Content hashes and embedding provenance reject stale documents or changed models before rebuild.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fraudlens_backend.settings import find_config_dir
from fraudlens_ml.rag import EmbeddingProvenance

if TYPE_CHECKING:
    from fraudlens_backend.rag.embedder import LlmClientEmbedder
    from fraudlens_backend.rag.factory import RagEmbeddingConfig


class CorpusVector(BaseModel):
    """A vector bound to an exact public regulatory chunk by its SHA-256 digest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text_hash: str = Field(..., pattern=r"^[a-f0-9]{64}$", description="SHA-256 of chunk UTF-8.")
    embedding: list[float] = Field(..., min_length=1, description="Finite learned embedding.")


class CorpusSnapshot(BaseModel):
    """Portable, reviewable public embeddings and their model-space identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provenance: EmbeddingProvenance = Field(..., description="Exact document embedding space.")
    vectors: list[CorpusVector] = Field(..., min_length=1, description="Public corpus vectors.")
    input_tokens: int = Field(..., ge=0, description="Provider-reported corpus input tokens.")
    cost_usd: Decimal = Field(..., ge=0, description="Catalog-priced corpus generation USD.")

    @model_validator(mode="after")
    def _validate_vectors(self) -> CorpusSnapshot:
        """Reject duplicate hashes, invalid dimensions, non-finite or zero document vectors."""
        hashes = [item.text_hash for item in self.vectors]
        if len(set(hashes)) != len(hashes):
            raise ValueError("duplicate corpus embedding hash")
        for item in self.vectors:
            if len(item.embedding) != self.provenance.dimensions:
                raise ValueError("corpus embedding dimension mismatch")
            if not all(math.isfinite(value) for value in item.embedding) or not any(item.embedding):
                raise ValueError("invalid corpus embedding")
        return self


def snapshot_path(config: RagEmbeddingConfig) -> Path:
    """Anchor a relative artifact path at the repository/container root beside config/."""
    path = Path(config.snapshot_file)
    return path if path.is_absolute() else find_config_dir().parent / path


class SnapshotEmbedder:
    """Serve only verified public document embeddings; queries require the live adapter."""

    def __init__(self, config: RagEmbeddingConfig) -> None:
        """Load and validate the artifact before an existing collection can be replaced."""
        snapshot = CorpusSnapshot.model_validate_json(snapshot_path(config).read_text())
        self.provenance = EmbeddingProvenance(
            kind="provider",
            model_id=config.model,
            dimensions=config.dimensions,
            rag_version=config.rag_version,
        )
        if snapshot.provenance != self.provenance:
            raise ValueError("corpus embedding provenance mismatch; refresh the snapshot")
        self._vectors = {item.text_hash: item.embedding for item in snapshot.vectors}

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Reject stale/missing chunks and return independent vector rows in input order."""
        hashes = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts]
        try:
            return [list(self._vectors[digest]) for digest in hashes]
        except KeyError:
            raise ValueError("corpus changed; refresh the public embedding snapshot") from None

    def embed_query(self, text: str) -> list[float]:
        """Never approximate a semantic query with a document or a hashing vector."""
        raise RuntimeError("snapshot contains document embeddings only")


def refresh_snapshot(
    texts: Sequence[str], config: RagEmbeddingConfig, embedder: LlmClientEmbedder
) -> CorpusSnapshot:
    """Generate the public artifact explicitly; replace its file only after validation."""
    rows = embedder.embed_documents(texts)
    snapshot = CorpusSnapshot(
        provenance=embedder.provenance,
        vectors=[
            CorpusVector(text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(), embedding=row)
            for text, row in zip(texts, rows, strict=True)
        ],
        input_tokens=embedder.input_tokens,
        cost_usd=embedder.cost_usd,
    )
    destination = snapshot_path(config)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_suffix(".pending")
    staging.write_text(snapshot.model_dump_json(indent=2) + "\n", encoding="utf-8")
    staging.replace(destination)
    return snapshot

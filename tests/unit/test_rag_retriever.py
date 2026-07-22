"""Phase 6 RAG retriever tests (plan §16 Phase 6: "top-k relevance on a known query; empty
index graceful []+flag; embeddings-down -> lexical fallback"). Build a small index in a temp
dir, then assert vector retrieval, the lexical embeddings-down fallback, empty/missing-index
handling, and the index_status readiness classification."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from fraudlens_ml.rag import (
    Embedder,
    EmbeddingProvenance,
    HashingEmbedder,
    RegulationDocument,
    Retriever,
    build_index,
    chunk_corpus,
    index_status,
)

_COLLECTION = "fincen_test"


def _doc(doc_id: str, citation: str, text: str) -> RegulationDocument:
    return RegulationDocument(
        doc_id=doc_id, title=f"title-{doc_id}", citation=citation, source="FinCEN", text=text
    )


_CORPUS = [
    _doc(
        "structuring",
        "31 CFR 1010.314",
        "Structuring breaks a large cash deposit into several smaller deposits below the ten "
        "thousand dollar reporting threshold to evade a currency transaction report.",
    ),
    _doc(
        "sar",
        "31 USC 5318(g)",
        "A suspicious activity report describes suspected money laundering with a clear narrative "
        "of who what when where and why.",
    ),
    _doc(
        "geography",
        "31 CFR 1010.610",
        "Wire transfers to high risk sanctioned jurisdictions require enhanced due diligence and "
        "source of funds verification.",
    ),
]


class _DownEmbedder:
    """An embedder whose calls fail — simulates a down/keyless embeddings provider (§10.6)."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("embeddings provider down")

    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("embeddings provider down")


class _LiveEmbedder:
    """A live-space test embedder that must not query a hashing-space index."""

    provenance = EmbeddingProvenance(
        kind="provider",
        model_id="openrouter/openai/text-embedding-3-small",
        dimensions=1536,
        rag_version="rag-v2-te3s",
    )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.0] * self.provenance.dimensions for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("provenance mismatch must fail closed before provider query")


@pytest.fixture(scope="module")
def index_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the small FinCEN test index once for the module."""
    persist = tmp_path_factory.mktemp("rag-index") / "chroma"
    build_index(
        chunk_corpus(_CORPUS, chunk_size=400, overlap=40),
        embedder=HashingEmbedder(),
        persist_dir=persist,
        collection=_COLLECTION,
    )
    return persist


def _retriever(persist: Path, embedder: Embedder) -> Retriever:
    return Retriever(
        persist_dir=persist, collection=_COLLECTION, embedder=embedder, rag_version="rag-v1"
    )


def test_vector_retrieval_returns_the_relevant_citation_first(index_dir: Path) -> None:
    result = _retriever(index_dir, HashingEmbedder()).retrieve(
        "customer made several cash deposits just below the reporting threshold to avoid a CTR",
        top_k=2,
    )
    assert result.mode == "vector"
    assert result.rag_version == "rag-v1"
    assert result.chunks[0].citation == "31 CFR 1010.314"
    assert all(isinstance(chunk.score, float) for chunk in result.chunks)


def test_vector_retrieval_filters_chunks_below_similarity_floor(index_dir: Path) -> None:
    retriever = Retriever(
        persist_dir=index_dir,
        collection=_COLLECTION,
        embedder=HashingEmbedder(),
        min_similarity=0.5,
    )
    result = retriever._from_vector(
        {
            "ids": [["strong", "weak"]],
            "documents": [["strong text", "weak text"]],
            "metadatas": [[{"citation": "A"}, {"citation": "B"}]],
            "distances": [[0.2, 0.8]],
        }
    )
    assert [chunk.chunk_id for chunk in result.chunks] == ["strong"]
    assert result.chunks[0].score == pytest.approx(0.8)


def test_retriever_rejects_invalid_similarity_floor(index_dir: Path) -> None:
    with pytest.raises(ValueError, match="min_similarity"):
        Retriever(
            persist_dir=index_dir,
            collection=_COLLECTION,
            embedder=HashingEmbedder(),
            min_similarity=1.1,
        )


def test_embeddings_down_falls_back_to_lexical(index_dir: Path) -> None:
    result = _retriever(index_dir, _DownEmbedder()).retrieve(
        "suspicious activity report narrative describing money laundering", top_k=2
    )
    assert result.mode == "lexical"
    assert result.chunks
    assert result.chunks[0].citation == "31 USC 5318(g)"


def test_provenance_mismatch_fails_closed_to_lexical(index_dir: Path) -> None:
    embedder = _LiveEmbedder()
    assert index_status(index_dir, _COLLECTION, embedder.provenance) == "mismatch"
    result = _retriever(index_dir, embedder).retrieve(
        "suspicious activity report narrative describing money laundering", top_k=2
    )
    assert result.mode == "lexical"
    assert result.rag_version == "rag-v2-te3s"
    assert result.chunks[0].citation == "31 USC 5318(g)"


def test_missing_index_returns_empty_result_with_flag(tmp_path: Path) -> None:
    result = _retriever(tmp_path / "absent", HashingEmbedder()).retrieve("anything")
    assert result.mode == "empty"
    assert result.chunks == []


def test_empty_index_returns_empty_result_with_flag(tmp_path: Path) -> None:
    persist = tmp_path / "chroma"
    build_index([], embedder=HashingEmbedder(), persist_dir=persist, collection=_COLLECTION)
    result = _retriever(persist, HashingEmbedder()).retrieve("anything")
    assert result.mode == "empty"
    assert result.chunks == []


def test_index_status_classifies_ready_missing_and_unknown_collection(
    index_dir: Path, tmp_path: Path
) -> None:
    assert index_status(index_dir, _COLLECTION) == "ready"
    assert index_status(tmp_path / "absent", _COLLECTION) == "missing"
    assert index_status(index_dir, "no_such_collection") == "missing"  # NotFoundError branch

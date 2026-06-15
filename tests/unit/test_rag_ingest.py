"""Phase 6 RAG ingestion tests (plan §16 Phase 6: "deterministic chunking"). Cover corpus
loading + frontmatter parsing, the deterministic word-packing chunker, the offline
HashingEmbedder, and the ChromaDB index builder (build + idempotent rebuild + empty)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from fraudlens_ml.rag import (
    HashingEmbedder,
    RegulationDocument,
    build_index,
    chunk_corpus,
    chunk_document,
    connect,
    load_corpus,
    tokenize,
)
from fraudlens_ml.rag.ingest import _parse_frontmatter
from fraudlens_ml.rag.retriever import index_status

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORPUS_DIR = _REPO_ROOT / "data" / "regulations"


def _doc(
    *,
    doc_id: str = "d1",
    text: str = "alpha beta gamma",
    citation: str = "31 CFR 1010.314",
    title: str = "T",
    source: str = "FinCEN",
) -> RegulationDocument:
    return RegulationDocument(
        doc_id=doc_id, title=title, citation=citation, source=source, text=text
    )


# --- corpus loading + frontmatter -------------------------------------------------


def test_load_corpus_reads_committed_provisions_in_order() -> None:
    docs = load_corpus(_CORPUS_DIR)
    ids = [d.doc_id for d in docs]
    assert len(docs) == 6
    assert ids == sorted(ids)  # deterministic, sorted-filename order
    by_id = {d.doc_id: d for d in docs}
    assert by_id["structuring-to-evade-reporting"].citation == "31 CFR 1010.314"
    assert all(d.text and d.title and d.source for d in docs)  # README.md skipped, bodies present


def test_parse_frontmatter_success() -> None:
    meta, body = _parse_frontmatter(
        "---\ndocId: a\ntitle: T\ncitation: C\nsource: S\n---\n\nbody text\n", where="x.md"
    )
    assert meta == {"docId": "a", "title": "T", "citation": "C", "source": "S"}
    assert body == "body text"


def test_parse_frontmatter_requires_fence() -> None:
    with pytest.raises(ValueError, match="missing frontmatter"):
        _parse_frontmatter("no fence here", where="x.md")


def test_parse_frontmatter_requires_all_keys() -> None:
    with pytest.raises(ValueError, match="missing metadata keys"):
        _parse_frontmatter("---\ndocId: a\ntitle: T\n---\nbody", where="x.md")


def test_parse_frontmatter_ignores_lines_without_a_colon() -> None:
    meta, _ = _parse_frontmatter(
        "---\ndocId: a\nnot a key value line\ntitle: T\ncitation: C\nsource: S\n---\nbody",
        where="x.md",
    )
    assert meta == {"docId": "a", "title": "T", "citation": "C", "source": "S"}


# --- deterministic chunking -------------------------------------------------------


def test_chunk_document_empty_text_yields_no_chunks() -> None:
    assert chunk_document(_doc(text="   ")) == []


def test_chunk_document_is_deterministic_overlapping_and_capped() -> None:
    doc = _doc(text=" ".join(f"word{i}" for i in range(200)))
    first = chunk_document(doc, chunk_size=120, overlap=40)
    second = chunk_document(doc, chunk_size=120, overlap=40)
    assert [c.model_dump() for c in first] == [c.model_dump() for c in second]  # deterministic
    assert len(first) > 1
    assert [c.chunk_id for c in first] == [f"{doc.doc_id}::{i}" for i in range(len(first))]
    assert all(len(c.text) <= 120 for c in first)  # chunk-size respected
    assert set(first[0].text.split()) & set(first[1].text.split())  # consecutive chunks overlap


def test_chunk_document_never_stalls_on_word_longer_than_chunk() -> None:
    doc = _doc(text="tiny " + "x" * 50 + " end")
    chunks = chunk_document(doc, chunk_size=10, overlap=3)
    assert any(len(c.text) > 10 for c in chunks)  # the long word gets its own chunk
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_chunk_corpus_preserves_document_order() -> None:
    docs = [_doc(doc_id="a", text="alpha beta"), _doc(doc_id="b", text="gamma delta")]
    assert [c.doc_id for c in chunk_corpus(docs, chunk_size=100, overlap=10)] == ["a", "b"]


# --- tokenizer + offline embedder -------------------------------------------------


def test_tokenize_lowercases_and_splits_alphanumeric() -> None:
    assert tokenize("Cash, Deposits! 10000") == ["cash", "deposits", "10000"]


def test_hashing_embedder_is_deterministic_and_unit_normalized() -> None:
    embedder = HashingEmbedder(dimensions=64)
    first = embedder.embed_query("structuring cash deposits")
    second = embedder.embed_query("structuring cash deposits")
    assert first == second
    assert len(first) == 64
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0, rel_tol=1e-9)
    batch = embedder.embed_documents(["a b c", "d e f"])
    assert len(batch) == 2 and all(len(row) == 64 for row in batch)


def test_hashing_embedder_empty_text_is_zero_vector() -> None:
    assert HashingEmbedder(dimensions=16).embed_query("") == [0.0] * 16  # zero-norm branch


# --- index build ------------------------------------------------------------------


def test_connect_creates_the_persist_directory(tmp_path: Path) -> None:
    connect(tmp_path / "made")
    assert (tmp_path / "made").is_dir()


def test_build_index_persists_chunks_and_is_idempotent(tmp_path: Path) -> None:
    docs = [
        _doc(doc_id="a", text="structuring cash deposits below the reporting threshold"),
        _doc(doc_id="b", citation="31 USC 5318", text="suspicious activity report narrative"),
    ]
    chunks = chunk_corpus(docs, chunk_size=200, overlap=20)
    persist = tmp_path / "chroma"
    assert build_index(
        chunks, embedder=HashingEmbedder(), persist_dir=persist, collection="cidx"
    ) == len(chunks)
    assert index_status(persist, "cidx") == "ready"
    # a rebuild drops + recreates the collection: still exactly len(chunks), no stale duplicates
    assert build_index(
        chunks, embedder=HashingEmbedder(), persist_dir=persist, collection="cidx"
    ) == len(chunks)


def test_build_index_with_no_chunks_creates_empty_collection(tmp_path: Path) -> None:
    persist = tmp_path / "chroma"
    assert (
        build_index([], embedder=HashingEmbedder(), persist_dir=persist, collection="cempty") == 0
    )
    assert index_status(persist, "cempty") == "empty"

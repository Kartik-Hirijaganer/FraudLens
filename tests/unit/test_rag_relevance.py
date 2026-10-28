"""Replay real public query embeddings against the shipped semantic corpus without network."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, TypeAdapter

from fraudlens_backend.rag import load_rag_embedding_config
from fraudlens_backend.rag.snapshot import SnapshotEmbedder
from fraudlens_ml.rag import HashingEmbedder, Retriever, build_index, chunk_corpus, load_corpus


class RelevanceCase(BaseModel):
    query: str = Field(..., description="Authored public regulatory test question.")
    expected: str = Field(..., description="Independently labeled relevant corpus document id.")


class RecordedQueries(SnapshotEmbedder):
    """Use recorded provider vectors for fixed public questions, never actual case queries."""

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def test_semantic_relevance_replays_real_vectors_and_published_metrics(sandbox: Path) -> None:
    config = load_rag_embedding_config()
    queries = config.model_copy(
        update={"snapshot_file": "tests/fixtures/rag-query-embeddings.json"}
    )
    cases = TypeAdapter(list[RelevanceCase]).validate_python(
        yaml.safe_load(Path("tests/fixtures/rag_relevance.yaml").read_text())
    )
    chunks = chunk_corpus(load_corpus(Path("data/regulations")))
    report = json.loads(Path("docs/reference/benchmarks/semantic-rag-default.json").read_text())
    for mode, index_embedder, query_embedder in (
        ("semantic", SnapshotEmbedder(config), RecordedQueries(queries)),
        ("hashing", HashingEmbedder(), HashingEmbedder()),
    ):
        directory = sandbox / mode
        build_index(
            chunks, embedder=index_embedder, persist_dir=directory, collection="regulations"
        )
        retriever = Retriever(
            persist_dir=directory,
            collection="regulations",
            embedder=query_embedder,
            min_similarity=0.2,
        )
        top1 = hits = 0
        for case, published in zip(cases, report["cases"], strict=True):
            result = retriever.retrieve(case.query, top_k=4)
            assert result.mode == "vector"
            assert case.query == published["query"]
            assert [chunk.doc_id for chunk in result.chunks] == [
                item["doc_id"] for item in published[mode]
            ]
            top1 += bool(result.chunks) and result.chunks[0].doc_id == case.expected
            hits += case.expected in {chunk.doc_id for chunk in result.chunks}
            if mode == "semantic":
                lexical = retriever.retrieve_local(case.query, top_k=4)
                assert [chunk.doc_id for chunk in lexical.chunks] == [
                    item["doc_id"] for item in published["lexical"]
                ]
        assert top1 / len(cases) == report["metrics"][mode]["top1_accuracy"]
        assert hits / len(cases) == report["metrics"][mode]["recall_at_4"]
    assert report["metrics"]["semantic"]["top1_accuracy"] >= 0.9
    assert report["metrics"]["semantic"]["recall_at_4"] == 1.0

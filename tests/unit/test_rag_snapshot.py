"""Semantic corpus snapshots build keylessly and reject stale or invalid vectors."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from fraudlens_backend.rag import LlmClientEmbedder, build_embedder, load_rag_embedding_config
from fraudlens_backend.rag.snapshot import (
    CorpusSnapshot,
    SnapshotEmbedder,
    refresh_snapshot,
    snapshot_path,
)
from fraudlens_backend.settings import AppSettings
from fraudlens_ml.rag import build_index, chunk_corpus, index_status, load_corpus


def test_committed_snapshot_builds_the_default_semantic_space_without_provider(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_provider(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("keyless build attempted a provider call")

    monkeypatch.setattr(LlmClientEmbedder, "embed_documents", reject_provider)
    settings = AppSettings()
    assert settings.rag_embedding_mode == "live"
    config = load_rag_embedding_config()
    embedder = SnapshotEmbedder(config)
    chunks = chunk_corpus(load_corpus(Path(settings.rag_corpus_dir)))
    assert build_index(
        chunks, embedder=embedder, persist_dir=sandbox, collection=settings.rag_collection
    ) == len(chunks)
    assert index_status(sandbox, settings.rag_collection, embedder.provenance) == "ready"
    assert embedder.provenance.kind == "provider"
    with pytest.raises(RuntimeError, match="document embeddings only"):
        embedder.embed_query("structuring")


def test_changed_corpus_does_not_destroy_an_existing_index(sandbox: Path) -> None:
    config = load_rag_embedding_config()
    embedder = SnapshotEmbedder(config)
    chunks = chunk_corpus(load_corpus(Path("data/regulations")))
    build_index(chunks, embedder=embedder, persist_dir=sandbox, collection="regulations")
    altered = chunks[0].model_copy(update={"text": "unreviewed changed provision"})
    with pytest.raises(ValueError, match="corpus changed"):
        build_index([altered], embedder=embedder, persist_dir=sandbox, collection="regulations")
    assert index_status(sandbox, "regulations", embedder.provenance) == "ready"


def test_snapshot_requires_matching_model_provenance() -> None:
    config = load_rag_embedding_config().model_copy(update={"rag_version": "another-space"})
    with pytest.raises(ValueError, match="provenance mismatch"):
        SnapshotEmbedder(config)


@pytest.mark.parametrize("damage", ["dimension", "duplicate", "nonfinite", "zero"])
def test_snapshot_rejects_invalid_vectors(damage: str) -> None:
    config = load_rag_embedding_config()
    value = CorpusSnapshot.model_validate_json(snapshot_path(config).read_text()).model_dump()
    if damage == "dimension":
        value["vectors"][0]["embedding"] = [1.0]
    elif damage == "duplicate":
        value["vectors"].append(value["vectors"][0])
    else:
        replacement = float("nan") if damage == "nonfinite" else 0.0
        value["vectors"][0]["embedding"] = [replacement] * config.dimensions
    with pytest.raises(ValidationError):
        CorpusSnapshot.model_validate(value)


def test_refresh_snapshot_writes_only_validated_public_vectors(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_rag_embedding_config().model_copy(
        update={"snapshot_file": str(sandbox / "vectors.json")}
    )
    embedder = build_embedder(AppSettings(rag_embedding_mode="live"), config=config)
    assert isinstance(embedder, LlmClientEmbedder)
    monkeypatch.setattr(
        embedder, "embed_documents", lambda texts: [[1.0] * config.dimensions for _ in texts]
    )
    snapshot = refresh_snapshot(["public regulation"], config, embedder)
    assert snapshot.vectors[0].text_hash == hashlib.sha256(b"public regulation").hexdigest()
    assert snapshot_path(config).is_file()
    assert not snapshot_path(config).with_suffix(".pending").exists()
    assert SnapshotEmbedder(config).embed_documents(["public regulation"]) == [
        [1.0] * config.dimensions
    ]

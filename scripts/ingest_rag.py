"""Summary: Build the FinCEN/BSA RAG index from the committed corpus (plan §16 Phase 6).
`make ingest-rag` chunks the public corpus and materializes reviewed semantic vectors without a
provider call. Explicit offline development may select hashing. Both paths persist provenance-tagged
ChromaDB collections at `settings.rag_index_dir` and work keylessly in image builds. When a database
is configured, ingestion records a `job_executions(ingest_rag)` row unless `--skip-db` is selected.
`make ingest-rag-live` explicitly refreshes public learned vectors through OpenRouter/Infisical.

Key classes:
- (none)

Key functions:
- ingest_corpus: load + chunk + embed + persist the index; return the chunk count + doc count.
- record_ingest_job: write a PHI-free `job_executions(ingest_rag)` row for the build.
- main: CLI entry point — build the index, optionally record the job, print a summary.

Notes:
- Relative corpus/index paths anchor at the repo root (like train_model.py), so the job is
  CWD-independent; `make ingest-rag` and the backend's /readyz probe resolve the same directory.
- `build_index` rebuilds the collection from scratch, so re-running is idempotent and a curated
  corpus never leaves stale chunks behind.
- The build is PHI-free by construction: the corpus is public regulatory text, and the recorded
  job payload carries only counts + paths, never document content.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import JobExecution, JobStatus, JobType
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.rag import LlmClientEmbedder, build_embedder, load_rag_embedding_config
from fraudlens_backend.rag.snapshot import SnapshotEmbedder, refresh_snapshot
from fraudlens_backend.settings import AppSettings, get_settings
from fraudlens_ml.rag import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    build_index,
    chunk_corpus,
    load_corpus,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _anchored(settings_value: str) -> Path:
    """Resolve a config path; relative values anchor at the repo root (CWD-independent)."""
    path = Path(settings_value)
    return path if path.is_absolute() else REPO_ROOT / path


def ingest_corpus(settings: AppSettings, *, chunk_size: int, overlap: int) -> tuple[int, int, Path]:
    """Load + chunk + embed + persist the index; return (chunk_count, doc_count, index_dir)."""
    corpus_dir = _anchored(settings.rag_corpus_dir)
    index_dir = _anchored(settings.rag_index_dir)
    documents = load_corpus(corpus_dir)
    chunks = chunk_corpus(documents, chunk_size=chunk_size, overlap=overlap)
    embedder = (
        SnapshotEmbedder(load_rag_embedding_config())
        if settings.rag_embedding_mode == "live"
        else build_embedder(settings)
    )
    count = build_index(
        chunks,
        embedder=embedder,
        persist_dir=index_dir,
        collection=settings.rag_collection,
    )
    return count, len(documents), index_dir


async def record_ingest_job(  # noqa: PLR0913 - records one audit row; extras are keyword-only
    session: AsyncSession,
    *,
    collection: str,
    documents: int,
    chunks: int,
    chunk_size: int,
    overlap: int,
) -> None:
    """Write a PHI-free `job_executions(ingest_rag)` row recording the build (counts only)."""
    session.add(
        JobExecution(
            agency_id=None,
            job_type=JobType.INGEST_RAG,
            status=JobStatus.SUCCEEDED,
            payload={"collection": collection, "chunk_size": chunk_size, "overlap": overlap},
            result={"documents": documents, "chunks": chunks},
            attempts=1,
        )
    )
    await session.flush()


async def _amain(
    chunk_size: int, overlap: int, *, refresh_embeddings: bool = False, skip_db: bool = False
) -> int:
    """Build the RAG index, record the job when a DB is configured, and print a summary."""
    settings = get_settings()
    if refresh_embeddings:
        source_documents = load_corpus(_anchored(settings.rag_corpus_dir))
        source_chunks = chunk_corpus(source_documents, chunk_size=chunk_size, overlap=overlap)
        embedder = build_embedder(settings)
        if not isinstance(embedder, LlmClientEmbedder):
            raise ValueError("snapshot refresh requires live embeddings")
        try:
            snapshot = refresh_snapshot(
                [chunk.text for chunk in source_chunks], load_rag_embedding_config(), embedder
            )
        finally:
            embedder.close()
        print(f"public embeddings: {snapshot.input_tokens} tokens, USD {snapshot.cost_usd}")
    chunks, documents, index_dir = ingest_corpus(settings, chunk_size=chunk_size, overlap=overlap)
    engine = None if skip_db else create_engine_from_settings(settings)
    if engine is None:
        print(f"ingest-rag OK: {chunks} chunks from {documents} docs -> {index_dir} (DB skipped)")
        return 0
    try:
        async with build_sessionmaker(engine)() as session:
            await record_ingest_job(
                session,
                collection=settings.rag_collection,
                documents=documents,
                chunks=chunks,
                chunk_size=chunk_size,
                overlap=overlap,
            )
            await session.commit()
    finally:
        await engine.dispose()
    print(f"ingest-rag OK: {chunks} chunks from {documents} docs -> {index_dir} (job recorded)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: build the FinCEN/BSA RAG index from the committed corpus."""
    parser = argparse.ArgumentParser(description="Build the FinCEN/BSA RAG index.")
    parser.add_argument(
        "--refresh-embeddings",
        action="store_true",
        help="Regenerate the public semantic vectors (paid provider call; approval required).",
    )
    parser.add_argument(
        "--skip-db", action="store_true", help="Build without a database audit write."
    )
    parser.add_argument(
        "--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="Max chars per chunk."
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=DEFAULT_CHUNK_OVERLAP,
        help="Char overlap between chunks.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(
        _amain(
            args.chunk_size,
            args.chunk_overlap,
            refresh_embeddings=args.refresh_embeddings,
            skip_db=args.skip_db,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

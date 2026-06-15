"""Summary: Build the FinCEN/BSA RAG index from the committed corpus (plan §16 Phase 6).
`make ingest-rag` loads every regulatory provision under `settings.rag_corpus_dir`, chunks it
deterministically, embeds the chunks with the offline `HashingEmbedder`, and persists a ChromaDB
collection at `settings.rag_index_dir` — with NO API keys, NO network, and NO cost, so it runs
the same locally, in `make local-demo`, and at image-build time (the index is baked into the
container in Phase 14). When a database is configured it records a `job_executions(ingest_rag)`
row for the ops audit trail; without one it still builds the index (so the build works in any
context). The live `text-embedding-3-small` embedder is the documented compliance-path seam (the
`Embedder` protocol), selected by config — not wired here, keeping this job keyless and offline.

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
from fraudlens_backend.settings import AppSettings, get_settings
from fraudlens_ml.rag import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    HashingEmbedder,
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
    count = build_index(
        chunks,
        embedder=HashingEmbedder(),
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


async def _amain(chunk_size: int, overlap: int) -> int:
    """Build the RAG index, record the job when a DB is configured, and print a summary."""
    settings = get_settings()
    chunks, documents, index_dir = ingest_corpus(settings, chunk_size=chunk_size, overlap=overlap)
    engine = create_engine_from_settings(settings)
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
        "--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="Max chars per chunk."
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=DEFAULT_CHUNK_OVERLAP,
        help="Char overlap between chunks.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args.chunk_size, args.chunk_overlap))


if __name__ == "__main__":
    raise SystemExit(main())

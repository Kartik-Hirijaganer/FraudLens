"""The one builder for a real, committed-corpus RAG index a test can point settings at.

The portfolio-demo bootstrap refuses without a queryable index (`assert_rag_index`), because the
quality-gated SAR cascade fails every draft that cites nothing. Tests that apply the story
therefore need a REAL index rather than a faked retriever.

It delegates to `scripts/ingest_rag.py`'s own `ingest_corpus`, so a test index is byte-identical
to what `make ingest-rag` produces and cannot drift from it. The offline hashing embedder is
deterministic and keyless, so a build costs roughly a tenth of a second and no money.
"""

from __future__ import annotations

from pathlib import Path

from fraudlens_backend.settings import AppSettings
from fraudlens_ml.rag import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE


def build_offline_rag_index(settings: AppSettings) -> Path:
    """Build the committed corpus into `settings.rag_index_dir` and return the index directory."""
    from ingest_rag import ingest_corpus  # noqa: PLC0415 - scripts/ is on sys.path via conftest

    _, _, index_dir = ingest_corpus(
        settings, chunk_size=DEFAULT_CHUNK_SIZE, overlap=DEFAULT_CHUNK_OVERLAP
    )
    return index_dir

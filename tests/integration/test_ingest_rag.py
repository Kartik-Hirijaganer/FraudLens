"""Phase 6 RAG ingest-script tests (plan §16 Phase 6: "make ingest-rag builds an index").
Build the index from the committed corpus into a temp dir, record the job row, and exercise
both `_amain` branches (DB configured vs not)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

import ingest_rag
from fraudlens_backend.db.models import JobExecution, JobType
from fraudlens_backend.settings import AppSettings
from fraudlens_ml.rag import index_status


def _settings(tmp_path: Path, **overrides: object) -> AppSettings:
    """Dev settings whose RAG index dir points at the test tmp (corpus stays the committed one)."""
    return AppSettings(environment="dev", rag_index_dir=str(tmp_path / "chroma"), **overrides)


def test_anchored_resolves_relative_against_repo_root_and_keeps_absolute(tmp_path: Path) -> None:
    assert ingest_rag._anchored("data/regulations") == ingest_rag.REPO_ROOT / "data" / "regulations"
    assert ingest_rag._anchored(str(tmp_path / "x")) == tmp_path / "x"


def test_ingest_corpus_builds_a_ready_index_from_the_committed_corpus(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    chunks, documents, index_dir = ingest_rag.ingest_corpus(settings, chunk_size=900, overlap=150)
    assert documents == 6
    assert chunks >= documents
    assert index_status(index_dir, settings.rag_collection) == "ready"


async def test_record_ingest_job_writes_one_platform_row(db_session: AsyncSession) -> None:
    await ingest_rag.record_ingest_job(
        db_session, collection="fincen_bsa", documents=6, chunks=12, chunk_size=900, overlap=150
    )
    count = (
        await db_session.execute(
            select(func.count())
            .select_from(JobExecution)
            .where(JobExecution.job_type == JobType.INGEST_RAG)
        )
    ).scalar_one()
    assert count == 1


def test_main_builds_index_and_skips_db_when_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(tmp_path, database_url=None)
    monkeypatch.setattr(ingest_rag, "get_settings", lambda: settings)
    assert ingest_rag.main([]) == 0
    out = capsys.readouterr().out
    assert "ingest-rag OK" in out and "DB skipped" in out
    assert index_status(tmp_path / "chroma", settings.rag_collection) == "ready"


async def test_amain_records_job_when_db_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    db_engine: AsyncEngine,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(ingest_rag, "get_settings", lambda: settings)
    monkeypatch.setattr(ingest_rag, "create_engine_from_settings", lambda _s: db_engine)
    assert await ingest_rag._amain(900, 150) == 0
    assert "job recorded" in capsys.readouterr().out

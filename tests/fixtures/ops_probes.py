"""Shared readiness-probe stubs for the ops suites.

The `/readyz` tests split across two modules under the 500-line cap (ADR-022): dependency probes in
`test_ops.py`, SAR model-route probes in `test_ops_llm_readiness.py`. These stubs live here rather
than in either one so the split did not fork a second copy of them (convention rule 5).
"""

from __future__ import annotations

from pathlib import Path

from fraudlens_ml.rag import HashingEmbedder, RegulationDocument, build_index, chunk_corpus


class FakeConn:
    """Async-context-manager connection used to stub a reachable database."""

    async def __aenter__(self) -> FakeConn:
        """Enter the stubbed connection scope."""
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        """Exit without suppressing anything."""
        return False

    async def execute(self, _statement: object) -> None:
        """Answer the bounded readiness SELECT."""
        return None


class OkEngine:
    """Engine stub whose connect() yields a working connection."""

    def connect(self) -> FakeConn:
        """Return a connection that succeeds."""
        return FakeConn()


class BadEngine:
    """Engine stub whose connect() fails (unreachable database)."""

    def connect(self) -> FakeConn:
        """Fail exactly as an unreachable database does."""
        raise OSError("connection refused")


def readiness_check(body: dict, name: str) -> dict:
    """Return a named dependency check from a /readyz body."""
    return next(check for check in body["checks"] if check["name"] == name)


def build_fixture_index(directory: Path, collection: str) -> Path:
    """Build a tiny ready ChromaDB index at a directory and return it (for the 'ok' probe)."""
    doc = RegulationDocument(
        doc_id="d", title="T", citation="31 CFR 1010.314", source="FinCEN", text="structuring cash"
    )
    build_index(
        chunk_corpus([doc]),
        embedder=HashingEmbedder(),
        persist_dir=directory,
        collection=collection,
    )
    return directory

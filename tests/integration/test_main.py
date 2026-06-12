"""Tests for the app factory: DB engine/sessionmaker wiring + lifespan disposal."""

from __future__ import annotations

from fastapi.testclient import TestClient

from fraudlens_backend.main import create_app
from fraudlens_backend.settings import AppSettings

_URL = "postgresql+asyncpg://user:pass@localhost:5432/fraudlens"


def test_app_without_database_url_has_no_engine() -> None:
    app = create_app(AppSettings(environment="dev"))
    assert app.state.db_engine is None
    assert app.state.db_sessionmaker is None


def test_app_with_database_url_builds_engine_and_disposes_on_shutdown() -> None:
    app = create_app(AppSettings(environment="dev", database_url=_URL))
    assert app.state.db_engine is not None
    assert app.state.db_sessionmaker is not None
    # Entering the TestClient context runs the lifespan startup + shutdown (engine
    # dispose); the engine is lazy so this needs no real database connection.
    with TestClient(app):
        pass

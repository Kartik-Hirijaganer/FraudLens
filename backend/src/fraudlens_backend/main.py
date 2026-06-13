"""Summary: FastAPI application factory for the FraudLens backend. create_app wires
settings into the app instance, configures structlog (JSON outside dev, console in
dev), builds the async DB engine + session factory from settings and disposes the
engine on shutdown via a lifespan, registers the FraudLens error-envelope handlers,
installs the gateway edge middleware stack (request-id, rate-limit, CORS allowlist,
security headers, access logging) in front of the in-process services, and mounts the
routers: the unprefixed ops probes (/healthz, /readyz) and the versioned business
surface under settings.api_v1_prefix (/api/v1/*). A module-level `app` is created for
uvicorn; tests call create_app(settings=...) to run with explicit configuration.

Key classes:
- (none)

Key functions:
- create_app: build and return a configured FastAPI application.

Notes:
- Settings are bound to app.state and read per-request via api.deps.get_app_settings,
  so a prod-mode test app cannot accidentally read the dev defaults.
- The DB engine on app.state.db_engine is None when no DATABASE_URL is configured, so
  the app still boots locally and /readyz reports the database as "skipped".
- The resolved RAG index dir on app.state.rag_index_dir is what the /readyz ChromaDB
  probe checks for index presence; tests override it to exercise ok/down/skipped.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from fraudlens_backend import __version__
from fraudlens_backend.api import ops
from fraudlens_backend.api.errors import register_exception_handlers
from fraudlens_backend.api.v1.router import api_router
from fraudlens_backend.db.session import (
    build_sessionmaker,
    create_engine_from_settings,
    dispose_engine,
)
from fraudlens_backend.middleware.gateway import install_gateway
from fraudlens_backend.middleware.logging import configure_logging
from fraudlens_backend.settings import AppSettings, get_settings


def _resolve_index_dir(settings: AppSettings) -> Path:
    """Resolve the RAG index dir; relative paths anchor at the process CWD (repo root / /app)."""
    index_dir = Path(settings.rag_index_dir)
    return index_dir if index_dir.is_absolute() else Path.cwd() / index_dir


def create_app(settings: AppSettings | None = None) -> FastAPI:
    """Construct a fully-configured FastAPI app (optionally with explicit settings)."""
    resolved = settings or get_settings()
    configure_logging(resolved.log_level, json_logs=resolved.environment != "dev")
    engine = create_engine_from_settings(resolved)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Dispose the DB engine's connection pool on application shutdown."""
        try:
            yield
        finally:
            if engine is not None:
                await dispose_engine(engine)

    app = FastAPI(
        title=resolved.app_name,
        version=__version__,
        summary="FraudLens AML fraud investigation API (walking skeleton).",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.db_engine = engine
    app.state.db_sessionmaker = build_sessionmaker(engine) if engine is not None else None
    app.state.rag_index_dir = _resolve_index_dir(resolved)
    register_exception_handlers(app)
    install_gateway(app, resolved)
    app.include_router(ops.router)
    app.include_router(api_router, prefix=resolved.api_v1_prefix)
    return app


app = create_app()

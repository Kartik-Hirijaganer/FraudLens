"""Summary: FastAPI application factory for the FraudLens backend. create_app
wires settings into the app instance, installs structured logging + the request-id
middleware, registers the Aegis error-envelope handlers, and mounts the routers:
the unprefixed ops probes (/healthz, /readyz) and the versioned business surface
under settings.api_v1_prefix (/api/v1/*). A module-level `app` is created for
uvicorn; tests call create_app(settings=...) to run with explicit configuration.

Key classes:
- (none)

Key functions:
- create_app: build and return a configured FastAPI application.

Notes:
- Settings are bound to app.state and read per-request via api.deps.get_app_settings,
  so a prod-mode test app cannot accidentally read the dev defaults.
"""

from __future__ import annotations

from fastapi import FastAPI

from fraudlens_backend import __version__
from fraudlens_backend.api import ops
from fraudlens_backend.api.errors import register_exception_handlers
from fraudlens_backend.api.v1.router import api_router
from fraudlens_backend.middleware.logging import RequestContextMiddleware, configure_logging
from fraudlens_backend.settings import AppSettings, get_settings


def create_app(settings: AppSettings | None = None) -> FastAPI:
    """Construct a fully-configured FastAPI app (optionally with explicit settings)."""
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    app = FastAPI(
        title=resolved.app_name,
        version=__version__,
        summary="FraudLens AML fraud investigation API (walking skeleton).",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = resolved
    app.add_middleware(RequestContextMiddleware, request_id_header=resolved.request_id_header)
    register_exception_handlers(app)
    app.include_router(ops.router)
    app.include_router(api_router, prefix=resolved.api_v1_prefix)
    return app


app = create_app()

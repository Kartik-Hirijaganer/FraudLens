"""Summary: Interactive and standalone Scalar API-reference rendering.

Key classes:
- (none)

Key functions:
- scalar_api_reference: render Scalar against the live or embedded OpenAPI document.

Notes:
- The browser asset origin is configuration-owned so the docs CSP and runtime URL remain aligned.
- Telemetry and persisted authentication are disabled for both live and generated references.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scalar_fastapi import get_scalar_api_reference
from starlette.responses import HTMLResponse

if TYPE_CHECKING:
    from fastapi import FastAPI

    from fraudlens_backend.settings import AppSettings


def scalar_api_reference(
    app: FastAPI,
    settings: AppSettings,
    *,
    embed_schema: bool = False,
) -> HTMLResponse:
    """Render Scalar against the live schema URL or an embedded standalone schema."""
    return get_scalar_api_reference(
        openapi_url=None if embed_schema else app.openapi_url,
        content=app.openapi() if embed_schema else None,
        title=f"{settings.app_name} API reference",
        scalar_js_url=settings.scalar_js_url,
        scalar_favicon_url="",
        persist_auth=False,
        telemetry=False,
    )

"""Summary: The read-only model-registry API (plan §5.3, §16 Phase 5 — `GET
/api/v1/model-versions`). It exposes the global model registry (`model_versions`) plus which
version is currently ACTIVE, so an operator can see the trained candidate(s), their gate
metrics, and the live model. The registry is PLATFORM-global (models are not tenant-scoped,
ADR-015), so these routes are not agency-filtered — but they still require a valid JWT
(`get_tenant`), failing closed like every business route; mutating model-lifecycle routes
(retrain/approve/canary/rollback) and admin RBAC land in Phase 10. Responses are PHI-free by
construction: feature NAMES + numeric metrics only.

Key classes:
- (none)

Key functions:
- list_model_versions: GET /model-versions — the registry, newest first, + the active label.
- get_model_version: GET /model-versions/{versionId} — one version (404 when absent).

Notes:
- `build_pointer` resolves the active deployment so the active label shown here is exactly the
  one the scorer's artifact cache would load (single source of truth, plan §10.5).
- A missing version id returns the `model_version_not_found` envelope (404), never a leak.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from fraudlens_backend.api.deps import DbSessionDep, get_tenant
from fraudlens_backend.db.models import ModelVersion
from fraudlens_backend.db.repositories.model_registry import ModelRegistryRepository
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.models.model_versions import (
    ModelVersionListResponse,
    ModelVersionResponse,
)

router = APIRouter(tags=["model-versions"])

TenantDep = Annotated[TenantContext, Depends(get_tenant)]


def _to_response(version: ModelVersion) -> ModelVersionResponse:
    """Project a persisted ModelVersion row onto the API response model."""
    return ModelVersionResponse(
        version_id=str(version.id),
        version_label=version.version_label,
        status=version.status,
        artifact_uri=version.artifact_uri,
        feature_spec=dict(version.feature_spec or {}),
        metrics=dict(version.metrics or {}),
        notes=version.notes,
        created_at=version.created_at,
    )


@router.get("/model-versions", response_model=ModelVersionListResponse)
async def list_model_versions(tenant: TenantDep, session: DbSessionDep) -> ModelVersionListResponse:
    """Return the model registry (newest first) plus the active version label."""
    repo = ModelRegistryRepository(session)
    versions = await repo.list_versions()
    pointer = await repo.build_pointer()
    return ModelVersionListResponse(
        versions=[_to_response(version) for version in versions],
        active_version_label=pointer.active_version_label if pointer is not None else None,
    )


@router.get("/model-versions/{version_id}", response_model=ModelVersionResponse)
async def get_model_version(
    version_id: uuid.UUID, tenant: TenantDep, session: DbSessionDep
) -> ModelVersionResponse:
    """Return one model-registry version by id; 404 when it does not exist."""
    version = await ModelRegistryRepository(session).get_version(version_id)
    if version is None:
        raise AppError("model_version_not_found")
    return _to_response(version)

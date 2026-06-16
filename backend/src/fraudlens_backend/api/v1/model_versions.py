"""Summary: The read-only model-registry API (plan §5.3, §16 Phase 5 — `GET
/api/v1/model-versions`). It exposes the global model registry (`model_versions`) plus which
version is currently ACTIVE, so an operator can see the trained candidate(s), their gate
metrics, and the live model. The registry is PLATFORM-global (models are not tenant-scoped,
ADR-015), so these routes are not agency-filtered — but they require an ADMIN JWT
(`get_admin_tenant`), failing closed like every business route (admin RBAC, plan §6.3 / §5.3);
the mutating model-lifecycle routes (retrain/shadow/approve/canary/rollback/drift) live in
`api/v1/model_lifecycle.py` (Phase 10). Responses are PHI-free by construction: feature NAMES +
numeric metrics only.

Key classes:
- (none)

Key functions:
- to_version_response: project a ModelVersion row onto the API response (shared with lifecycle).
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

from fastapi import APIRouter, Depends, Path

from fraudlens_backend.api.deps import DbSessionDep, get_admin_tenant
from fraudlens_backend.db.models import ModelVersion
from fraudlens_backend.db.repositories.model_registry import ModelRegistryRepository
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.models.model_versions import (
    ModelVersionListResponse,
    ModelVersionResponse,
)

router = APIRouter(tags=["model-versions"])

# Model lifecycle is admin-only (plan §5.3 endpoints 19-26; routes.yaml `required_role: admin`).
TenantDep = Annotated[TenantContext, Depends(get_admin_tenant)]


def to_version_response(version: ModelVersion) -> ModelVersionResponse:
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
        versions=[to_version_response(version) for version in versions],
        active_version_label=pointer.active_version_label if pointer is not None else None,
    )


@router.get("/model-versions/{versionId}", response_model=ModelVersionResponse)
async def get_model_version(
    version_id: Annotated[uuid.UUID, Path(alias="versionId")],
    tenant: TenantDep,
    session: DbSessionDep,
) -> ModelVersionResponse:
    """Return one model-registry version by id; 404 when it does not exist."""
    version = await ModelRegistryRepository(session).get_version(version_id)
    if version is None:
        raise AppError("model_version_not_found")
    return to_version_response(version)

"""Summary: Read access to the platform model registry (plan §16 Phase 5, §9.2). The model
registry (`model_versions`) and the single active/canary pointer (`model_deployments`) are
PLATFORM tables — models are global, NOT tenant-scoped (ADR-015) — so this repository does NOT
extend `TenantScopedRepository`; it reads the shared registry like `AgencyRepository` reads
`agencies`. It backs the read-only `GET /api/v1/model-versions` API and — crucially — resolves
the active deployment into the `fraudlens_ml` `DeploymentPointer` the scorer's artifact cache
loads from (`build_pointer`): the active version (+ the previous active as the last-known-good
fallback, plan §10.6). This is the seam between the DB registry and the heavy-ML scorer; the
backend may import `fraudlens-ml` (it is `fraudlens-ml` that must never import the backend).

Key classes:
- ModelRegistryRepository: read-only access to model_versions + the deployment pointer.

Key functions:
- (none)

Notes:
- `build_pointer` returns None when no deployment row or no active version exists, so callers
  (readiness, the pipeline) can fail closed rather than score with no model (plan §10.6).
- The previous-active version is folded into the pointer only when both its label and uri
  resolve, so the scorer's last-known-good fallback is offered only when it is loadable.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import ModelDeployment, ModelVersion
from fraudlens_ml.scoring import DeploymentPointer

_DEFAULT_LIST_LIMIT = 50


class ModelRegistryRepository:
    """Read-only access to the global model registry + the active deployment pointer."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the session; the registry is platform-global (no agency scope)."""
        self._session = session

    async def list_versions(
        self, *, limit: int = _DEFAULT_LIST_LIMIT, offset: int = 0
    ) -> Sequence[ModelVersion]:
        """Return registry versions newest-first (the whole registry is small)."""
        stmt = (
            select(ModelVersion)
            .order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def get_version(self, version_id: uuid.UUID) -> ModelVersion | None:
        """Return one registry version by id, or None when it does not exist."""
        return await self._session.get(ModelVersion, version_id)

    async def get_active_deployment(self) -> ModelDeployment | None:
        """Return the single live deployment pointer row, or None when unset."""
        return (await self._session.execute(select(ModelDeployment).limit(1))).scalar_one_or_none()

    async def build_pointer(self) -> DeploymentPointer | None:
        """Resolve the active deployment into the scorer's DeploymentPointer (+ last-known-good)."""
        deployment = await self.get_active_deployment()
        if deployment is None:
            return None
        active = await self._session.get(ModelVersion, deployment.active_version_id)
        if active is None:
            return None
        previous_label: str | None = None
        previous_uri: str | None = None
        if deployment.previous_active_version_id is not None:
            previous = await self._session.get(ModelVersion, deployment.previous_active_version_id)
            if previous is not None:
                previous_label = previous.version_label
                previous_uri = previous.artifact_uri
        return DeploymentPointer(
            active_version_label=active.version_label,
            active_artifact_uri=active.artifact_uri,
            previous_version_label=previous_label,
            previous_artifact_uri=previous_uri,
        )

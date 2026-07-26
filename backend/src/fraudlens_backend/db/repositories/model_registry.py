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
- `build_latest_candidate_pointer` is a non-mutating resolver for explicitly gated callers; it
  never promotes a candidate or creates a deployment row.
- The previous-active version is folded into the pointer only when both its label and uri
  resolve, so the scorer's last-known-good fallback is offered only when it is loadable.
- `build_canary_deployment` resolves the active + optional canary (at its percent) into the
  `fraudlens_ml` `CanaryDeployment` the wiring routes per-transaction through (Phase 10, §10.5);
  the canary arm is offered only when its version row resolves (else it stays active-only).
- `FIXTURE_MODEL_LABEL` names the synthetic bundle the foundation seed installs as the initial
  active pointer. It lives beside `build_pointer` because both the seed (which writes it) and the
  portfolio-demo bootstrap (which may displace it) must agree on one value, not two copies.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import ModelDeployment, ModelVersion, ModelVersionStatus
from fraudlens_ml.scoring import CanaryDeployment, DeploymentPointer

_DEFAULT_LIST_LIMIT = 50

# The committed synthetic bundle the foundation seed registers and points at, so a fresh database
# always has a loadable active model. It is the ONLY active label a real promotion may displace
# automatically — anything else is an operator's deliberate choice.
FIXTURE_MODEL_LABEL = "v0-fixture"


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

    async def get_version_by_label(self, version_label: str) -> ModelVersion | None:
        """Return the registry version with this label, or None (maps a scored label to its id)."""
        stmt = select(ModelVersion).where(ModelVersion.version_label == version_label)
        return (await self._session.execute(stmt)).scalar_one_or_none()

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

    async def build_latest_candidate_pointer(self) -> DeploymentPointer | None:
        """Resolve the newest candidate for an explicitly enabled non-production fallback."""
        stmt = (
            select(ModelVersion)
            .where(ModelVersion.status == ModelVersionStatus.CANDIDATE)
            .order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc())
            .limit(1)
        )
        candidate = (await self._session.execute(stmt)).scalar_one_or_none()
        if candidate is None:
            return None
        return DeploymentPointer(
            active_version_label=candidate.version_label,
            active_artifact_uri=candidate.artifact_uri,
        )

    async def build_canary_deployment(self) -> CanaryDeployment | None:
        """Resolve the active (+ optional canary at its percent) into a CanaryDeployment.

        Returns None when no deployment row or no active version exists. The canary arm is
        populated only when the deployment names a canary version that resolves; the percent
        rides along so the wiring's `CanaryRouter` decides per-transaction (plan §10.5).
        """
        deployment = await self.get_active_deployment()
        if deployment is None:
            return None
        active = await self._session.get(ModelVersion, deployment.active_version_id)
        if active is None:
            return None
        canary_label: str | None = None
        canary_uri: str | None = None
        if deployment.canary_version_id is not None:
            canary = await self._session.get(ModelVersion, deployment.canary_version_id)
            if canary is not None:
                canary_label = canary.version_label
                canary_uri = canary.artifact_uri
        return CanaryDeployment(
            active_version_label=active.version_label,
            active_artifact_uri=active.artifact_uri,
            canary_version_label=canary_label,
            canary_artifact_uri=canary_uri,
            canary_percent=deployment.canary_percent,
        )

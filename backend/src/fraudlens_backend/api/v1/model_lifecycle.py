"""Summary: The admin model-lifecycle API (plan §5.3, §5.4, §10.5/§10.5.1, §16 Phase 10 —
endpoints 19-26). Every route requires the ADMIN role (`get_admin_tenant`; a non-admin fails
closed with `admin_role_required`, 403) and drives the human-gated lifecycle the read-only
`model_versions` API deliberately omits: trigger a retrain Job (candidate-only; 422 when matured
labels are insufficient, 409 when one is already running), promote a candidate candidate→shadow
(only with a passing eval), human-approve a shadow, ramp a canary 5/25/50→100% (100 flips the
active pointer with no redeploy), roll back (abort a canary or restore the previous active),
evaluate the canary auto-abort guard, and list training runs + advisory drift reports. The actual
training/drift compute runs out-of-band as a Container Apps Job (`scripts/{retrain,drift_scan}.py`)
— the trigger here submits it through the config-driven job backend and acknowledges with 202.
Every mutation writes a PHI-free `audit_logs` row (plan §8.4).

Key classes:
- (none)

Key functions:
- trigger_training_run: POST /training-runs — submit a retrain Job (eligibility-gated).
- list_training_runs: GET /training-runs — the training-run history.
- promote_to_shadow: POST /model-versions/{id}/shadow — candidate→shadow (needs a passing eval).
- approve_version: POST /model-versions/{id}/approve — the human approval gate (shadow only).
- set_canary: POST /model-versions/{id}/canary — ramp the canary (100% promotes to active).
- get_deployment: GET /model-deployment — the live active/canary pointer.
- rollback_deployment: POST /model-deployment/rollback — abort a canary / restore previous active.
- evaluate_canary: POST /model-deployment/canary/evaluate — run the §10.5.1 auto-abort guard.
- list_drift_reports: GET /drift-reports — advisory drift reports (never gate serving).

Notes:
- The pointer flip (canary 100% → active, or rollback) is an in-place `model_deployments` write, so
  a running process reloads on the next run with NO redeploy (plan §5.4); the scorer's cache keys by
  version label, so the new model is served immediately and a rollback is served warm.
- Promotion is candidate-only at trigger time: a retrain never touches the active/canary pointer —
  only the explicit shadow→approve→canary→activate flow here can, and only for an admin.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.api.deps import (
    DbSessionDep,
    SettingsDep,
    audit_writer,
    get_admin_tenant,
    require_actor,
)
from fraudlens_backend.api.v1.model_versions import to_version_response
from fraudlens_backend.backends.azure import BackendConfigurationError, BackendRequestError
from fraudlens_backend.backends.jobs import get_job_backend
from fraudlens_backend.db.models import (
    DriftReport,
    JobType,
    ModelDeployment,
    ModelTrainingRun,
    ModelVersion,
)
from fraudlens_backend.db.repositories import ModelLifecycleRepository, ModelRegistryRepository
from fraudlens_backend.db.repositories.model_lifecycle import (
    can_approve,
    can_canary,
    can_shadow,
    canary_should_abort,
    labels_eligible,
)
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.models.model_lifecycle import (
    CanaryEvaluationResponse,
    CanaryRequest,
    DeploymentResponse,
    DriftReportListResponse,
    DriftReportView,
    RollbackResponse,
    TrainingRunListResponse,
    TrainingRunTriggerResponse,
    TrainingRunView,
    TriggerTrainingRequest,
)
from fraudlens_backend.models.model_versions import ModelVersionResponse

router = APIRouter(tags=["model-lifecycle"])

AdminDep = Annotated[TenantContext, Depends(get_admin_tenant)]

_DEFAULT_PAGE_LIMIT = 50
_MAX_PAGE_LIMIT = 200


def _to_training_run_view(run: ModelTrainingRun) -> TrainingRunView:
    """Project a persisted training run onto its PHI-free API view."""
    return TrainingRunView(
        training_run_id=str(run.id),
        trigger=run.trigger.value,
        status=run.status.value,
        dataset_id=str(run.dataset_id),
        artifact_uri=run.artifact_uri,
        metrics=dict(run.metrics or {}),
        created_at=run.created_at,
    )


async def _to_deployment_response(
    deployment: ModelDeployment, session: AsyncSession
) -> DeploymentResponse:
    """Project the deployment pointer onto its API view, resolving version ids to labels.

    Refreshes first so the server-side `updated_at` (onupdate, expired after a mutating flush) is
    reloaded in the async context — reading it lazily during sync serialization would do unexpected
    IO (a greenlet error). The refresh is a cheap single-row reload.
    """
    await session.refresh(deployment)

    async def label(version_id: uuid.UUID | None) -> str | None:
        if version_id is None:
            return None
        version = await session.get(ModelVersion, version_id)
        return version.version_label if version is not None else None

    return DeploymentResponse(
        active_version_label=await label(deployment.active_version_id) or "",
        canary_version_label=await label(deployment.canary_version_id),
        canary_percent=deployment.canary_percent,
        previous_active_version_label=await label(deployment.previous_active_version_id),
        updated_at=deployment.updated_at,
    )


async def _to_drift_view(report: DriftReport, session: AsyncSession) -> DriftReportView:
    """Project an advisory drift report onto its API view, resolving the model version label."""
    version = await session.get(ModelVersion, report.model_version_id)
    return DriftReportView(
        drift_report_id=str(report.id),
        version_label=version.version_label if version is not None else "",
        window=report.window,
        severity=report.severity,
        advisory=report.advisory,
        metrics=dict(report.metrics or {}),
        created_at=report.created_at,
    )


@router.post("/training-runs", status_code=202, response_model=TrainingRunTriggerResponse)
async def trigger_training_run(
    request: Request,
    tenant: AdminDep,
    session: DbSessionDep,
    settings: SettingsDep,
    payload: TriggerTrainingRequest | None = None,
) -> TrainingRunTriggerResponse:
    """Submit a retrain Job (candidate-only); 422 if labels insufficient, 409 if one is running."""
    body = payload or TriggerTrainingRequest()
    lifecycle = ModelLifecycleRepository(session)
    if await lifecycle.training_in_progress():
        raise AppError("training_in_progress")
    counts = await lifecycle.matured_label_counts(as_of=datetime.now(UTC))
    if not labels_eligible(
        counts,
        min_total=settings.retrain_min_labels_total,
        min_per_class=settings.retrain_min_labels_per_class,
    ):
        raise AppError("insufficient_matured_labels")
    actor = require_actor(tenant)
    try:
        job_id = get_job_backend(settings).submit(JobType.RETRAIN.value, {"trigger": body.trigger})
    except (BackendConfigurationError, BackendRequestError, RuntimeError) as exc:
        raise AppError("job_submission_failed") from exc
    await audit_writer(tenant, session, request).record(
        actor_id=actor,
        action="model.retrain_triggered",
        resource_type="model_training_run",
        resource_id=job_id,
        metadata={"trigger": body.trigger, "jobId": job_id},
    )
    await session.commit()
    return TrainingRunTriggerResponse(
        job_id=job_id,
        trigger=body.trigger,
        status="submitted",
        label_total=counts.total,
        label_positives=counts.positives,
        label_negatives=counts.negatives,
    )


@router.get("/training-runs", response_model=TrainingRunListResponse)
async def list_training_runs(
    tenant: AdminDep,
    session: DbSessionDep,
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE_LIMIT)] = _DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TrainingRunListResponse:
    """Return the model training-run history (newest first)."""
    runs = await ModelLifecycleRepository(session).list_training_runs(limit=limit, offset=offset)
    return TrainingRunListResponse(training_runs=[_to_training_run_view(run) for run in runs])


@router.post("/model-versions/{versionId}/shadow", response_model=ModelVersionResponse)
async def promote_to_shadow(
    version_id: Annotated[uuid.UUID, Path(alias="versionId")],
    request: Request,
    tenant: AdminDep,
    session: DbSessionDep,
) -> ModelVersionResponse:
    """Promote a candidate to shadow — only with a passing evaluation (plan §10.5.1)."""
    lifecycle = ModelLifecycleRepository(session)
    version = await ModelRegistryRepository(session).get_version(version_id)
    if version is None:
        raise AppError("model_version_not_found")
    if not can_shadow(
        version.status, has_passing_evaluation=await lifecycle.has_passing_evaluation(version.id)
    ):
        raise AppError("invalid_model_transition")
    await lifecycle.promote_to_shadow(version)
    await audit_writer(tenant, session, request).record(
        actor_id=require_actor(tenant),
        action="model.shadow",
        resource_type="model_version",
        resource_id=str(version.id),
        metadata={"versionLabel": version.version_label, "status": version.status.value},
    )
    await session.commit()
    return to_version_response(version)


@router.post("/model-versions/{versionId}/approve", response_model=ModelVersionResponse)
async def approve_version(
    version_id: Annotated[uuid.UUID, Path(alias="versionId")],
    request: Request,
    tenant: AdminDep,
    session: DbSessionDep,
) -> ModelVersionResponse:
    """Record the human approval on a shadow version (the §5.4 gate before any canary)."""
    lifecycle = ModelLifecycleRepository(session)
    version = await ModelRegistryRepository(session).get_version(version_id)
    if version is None:
        raise AppError("model_version_not_found")
    if not can_approve(version.status):
        raise AppError("invalid_model_transition")
    actor = require_actor(tenant)
    await lifecycle.approve(version, approved_by=actor)
    await audit_writer(tenant, session, request).record(
        actor_id=actor,
        action="model.approve",
        resource_type="model_version",
        resource_id=str(version.id),
        metadata={"versionLabel": version.version_label},
    )
    await session.commit()
    return to_version_response(version)


@router.post("/model-versions/{versionId}/canary", response_model=DeploymentResponse)
async def set_canary(
    version_id: Annotated[uuid.UUID, Path(alias="versionId")],
    payload: CanaryRequest,
    request: Request,
    tenant: AdminDep,
    session: DbSessionDep,
) -> DeploymentResponse:
    """Ramp the canary to a percent; 100% promotes it to active (pointer flip, no redeploy)."""
    lifecycle = ModelLifecycleRepository(session)
    version = await ModelRegistryRepository(session).get_version(version_id)
    if version is None:
        raise AppError("model_version_not_found")
    deployment = await lifecycle.get_deployment()
    if deployment is None:
        raise AppError("deployment_not_found")
    if not can_canary(version.status, approved=version.approved_at is not None):
        raise AppError("invalid_model_transition")
    actor = require_actor(tenant)
    if payload.percent == 100:  # noqa: PLR2004 - 100% is the documented "promote to active" step.
        await lifecycle.activate(version, updated_by=actor)
        action = "model.activate"
    else:
        await lifecycle.start_canary(version, percent=payload.percent, updated_by=actor)
        action = "model.canary"
    # `activate`/`start_canary` mutate the same session-identity deployment row in place.
    response = await _to_deployment_response(deployment, session)
    await audit_writer(tenant, session, request).record(
        actor_id=actor,
        action=action,
        resource_type="model_deployment",
        resource_id=str(version.id),
        metadata={"versionLabel": version.version_label, "percent": str(payload.percent)},
    )
    await session.commit()
    return response


@router.get("/model-deployment", response_model=DeploymentResponse)
async def get_deployment(tenant: AdminDep, session: DbSessionDep) -> DeploymentResponse:
    """Return the live active/canary deployment pointer (404 when none is configured)."""
    deployment = await ModelLifecycleRepository(session).get_deployment()
    if deployment is None:
        raise AppError("deployment_not_found")
    return await _to_deployment_response(deployment, session)


@router.post("/model-deployment/rollback", response_model=RollbackResponse)
async def rollback_deployment(
    request: Request, tenant: AdminDep, session: DbSessionDep
) -> RollbackResponse:
    """Abort an in-progress canary, else restore the previous active pointer (plan §10.5/§10.6)."""
    lifecycle = ModelLifecycleRepository(session)
    deployment = await lifecycle.get_deployment()
    if deployment is None:
        raise AppError("nothing_to_rollback")
    actor = require_actor(tenant)
    outcome = await lifecycle.rollback(updated_by=actor)
    if outcome is None:
        raise AppError("nothing_to_rollback")
    # `rollback` mutated the same session-identity deployment row in place.
    response = await _to_deployment_response(deployment, session)
    await audit_writer(tenant, session, request).record(
        actor_id=actor,
        action="model.rollback",
        resource_type="model_deployment",
        resource_id=outcome.active_version_label,
        metadata={"action": outcome.action, "activeVersionLabel": outcome.active_version_label},
    )
    await session.commit()
    return RollbackResponse(action=outcome.action, deployment=response)


@router.post("/model-deployment/canary/evaluate", response_model=CanaryEvaluationResponse)
async def evaluate_canary(
    request: Request, tenant: AdminDep, session: DbSessionDep, settings: SettingsDep
) -> CanaryEvaluationResponse:
    """Run the §10.5.1 canary auto-abort guard; roll back the canary when it deviates too far."""
    lifecycle = ModelLifecycleRepository(session)
    deployment = await lifecycle.get_deployment()
    if deployment is None:
        raise AppError("deployment_not_found")
    stats = await lifecycle.canary_inference_stats(deployment)
    has_canary = deployment.canary_version_id is not None
    aborted = False
    if has_canary and canary_should_abort(
        stats,
        min_samples=settings.canary_guard_min_samples,
        max_deviation=settings.canary_guard_max_deviation,
    ):
        await lifecycle.rollback(updated_by=require_actor(tenant))
        aborted = True
        await audit_writer(tenant, session, request).record(
            actor_id=require_actor(tenant),
            action="model.canary_auto_abort",
            resource_type="model_deployment",
            resource_id=str(deployment.active_version_id),
            metadata={
                "activeMean": f"{stats.active_mean:.4f}",
                "canaryMean": f"{stats.canary_mean:.4f}",
            },
        )
    # `rollback` (when it ran) mutated the same session-identity deployment row in place.
    response = await _to_deployment_response(deployment, session)
    await session.commit()
    return CanaryEvaluationResponse(
        aborted=aborted,
        active_count=stats.active_count,
        active_mean=stats.active_mean,
        canary_count=stats.canary_count,
        canary_mean=stats.canary_mean,
        deviation=abs(stats.canary_mean - stats.active_mean) if has_canary else 0.0,
        deployment=response,
    )


@router.get("/drift-reports", response_model=DriftReportListResponse)
async def list_drift_reports(
    tenant: AdminDep,
    session: DbSessionDep,
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE_LIMIT)] = _DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DriftReportListResponse:
    """Return advisory drift reports (newest first); these only signal, never gate serving."""
    reports = await ModelLifecycleRepository(session).list_drift_reports(limit=limit, offset=offset)
    return DriftReportListResponse(
        drift_reports=[await _to_drift_view(report, session) for report in reports]
    )

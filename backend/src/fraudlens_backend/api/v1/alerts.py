"""Summary: Tenant-scoped authoritative case workspace, queues and maker-checker commands.

Key classes:
- (none)

Key functions:
- list_alerts: capability-filtered queues.
- get_alert: evidence, frozen rounds and version history.
- act_on_alert: self pickup, comments and governed dismissal.
- save_sar: versioned working narrative including manual authoring.
- submit_case: idempotent immutable submission.
- decide_submission: independent atomic approve/return/decline.

Notes:
- The former SAR review and separate case closure routes are deliberately removed.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Path, Query, Request

from fraudlens_backend.api.deps import (
    DbSessionDep,
    Permission,
    SettingsDep,
    audit_writer,
    enforce_permission,
    get_tenant,
    optional_actor,
    require_actor,
)
from fraudlens_backend.backends.storage import get_storage_backend
from fraudlens_backend.db.models.enums import AlertStatus
from fraudlens_backend.db.repositories import (
    AgentExecutionRepository,
    AlertRepository,
    AnalysisRunRepository,
    SarDraftRepository,
    TransactionRepository,
)
from fraudlens_backend.models.agent_executions import agent_execution_to_view
from fraudlens_backend.models.alerts import (
    AlertActionRequest,
    AlertDetailResponse,
    AlertListResponse,
    AlertView,
)
from fraudlens_backend.models.case_workflow import (
    DecisionRequest,
    SubmissionRequest,
    WorkflowCommandResult,
    WorkingSarRequest,
)
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.models.sar import SarDraftView
from fraudlens_backend.sar.pdf import generate_sar_pdf
from fraudlens_backend.services.alert_workflow import AlertWorkflowService
from fraudlens_backend.services.case_views import action_view, project_case, submission_views
from fraudlens_backend.services.person_identity import resolve_actor
from fraudlens_backend.services.sar_regeneration import sar_draft_to_view, sar_model_input_for_view

router = APIRouter(tags=["alerts"])
TenantDep = Annotated[TenantContext, Depends(get_tenant)]
AlertId = Annotated[uuid.UUID, Path(alias="alertId")]
SubmissionId = Annotated[uuid.UUID, Path(alias="submissionId")]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]
Queue = Literal["available", "mine", "awaiting_review", "returned", "closed"]


def _workflow(
    tenant: TenantContext, session: DbSessionDep, request: Request
) -> AlertWorkflowService:
    """Bind trusted tenancy and request-correlated audit to the shared workflow."""
    return AlertWorkflowService(
        session,
        agency_id=uuid.UUID(tenant.agency_id),
        audit=audit_writer(tenant, session, request),
        low_confidence_margin=request.app.state.settings.review_low_confidence_margin,
    )


async def _view(
    tenant: TenantContext, session: DbSessionDep, alert_id: uuid.UUID, margin: float
) -> AlertView:
    """Reload authoritative case summary for a command response."""
    row = await AlertRepository(session, uuid.UUID(tenant.agency_id)).get_alert_summary(alert_id)
    if row is None:
        raise AppError("alert_not_found")
    return await project_case(
        session,
        agency_id=uuid.UUID(tenant.agency_id),
        actor_id=optional_actor(tenant),
        row=row,
        margin=margin,
    )


@router.get("/alerts", response_model=AlertListResponse)
async def list_alerts(  # noqa: PLR0913, PLR0917 - explicit FastAPI command and injected dependencies.
    tenant: TenantDep,
    session: DbSessionDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[AlertStatus | None, Query()] = None,
    queue: Annotated[Queue | None, Query()] = None,
) -> AlertListResponse:
    """Filter queues with backend ownership and checker eligibility before pagination."""
    agency_id = uuid.UUID(tenant.agency_id)
    person_id = None
    if queue is not None and queue != "closed":
        try:
            actor = await resolve_actor(session, agency_id, require_actor(tenant))
        except AppError:
            return AlertListResponse(alerts=[])
        if actor.role.value == "auditor" or (
            queue == "awaiting_review" and actor.role.value not in {"reviewer", "admin"}
        ):
            return AlertListResponse(alerts=[])
        person_id = actor.person_id
    repo = AlertRepository(session, agency_id)
    total = await repo.count_alerts(status=status, queue=queue, person_id=person_id)
    rows = await repo.list_alerts(
        limit=limit, offset=offset, status=status, queue=queue, person_id=person_id
    )
    views = [
        await project_case(
            session,
            agency_id=agency_id,
            actor_id=optional_actor(tenant),
            row=row,
            margin=settings.review_low_confidence_margin,
        )
        for row in rows
    ]
    return AlertListResponse(alerts=views, total=total)


@router.get("/alerts/{alertId}", response_model=AlertDetailResponse)
async def get_alert(
    alert_id: AlertId, tenant: TenantDep, session: DbSessionDep, settings: SettingsDep
) -> AlertDetailResponse:
    """Read case, frozen submission evidence, immutable versions and masked action history."""
    agency_id = uuid.UUID(tenant.agency_id)
    alert = await _view(tenant, session, alert_id, settings.review_low_confidence_margin)
    run_id = uuid.UUID(alert.run_id)
    sar_repo = SarDraftRepository(session, agency_id)
    draft = await sar_repo.get_for_run(run_id)
    repo = AnalysisRunRepository(session, agency_id)
    run = await repo.get(run_id)
    if run is None:
        raise AppError("investigation_not_found")
    model_input = sar_model_input_for_view(
        agency_id=agency_id,
        run=run,
        result=await repo.get_result(run_id),
        transaction=await TransactionRepository(session, agency_id).get(
            uuid.UUID(alert.transaction_id)
        ),
        retrieval=await repo.get_retrieval(run_id),
    )
    actions = await AlertRepository(session, agency_id).list_actions(alert_id)
    executions = await AgentExecutionRepository(session, agency_id).list_for_run(run_id)
    return AlertDetailResponse(
        alert=alert,
        sar_draft=sar_draft_to_view(draft, model_input=model_input) if draft else None,
        actions=[action_view(item) for item in actions],
        agent_executions=[agent_execution_to_view(item) for item in executions],
        workflow_mode=run.workflow_mode,
        graph_version=run.graph_version,
        revision_count=draft.revision_count if draft else 0,
        sar_content=draft.content if draft else None,
        submissions=await submission_views(session, agency_id, alert_id, optional_actor(tenant)),
        draft_versions=[
            sar_draft_to_view(item) for item in await sar_repo.list_for_alert(alert_id)
        ],
    )


@router.post("/alerts/{alertId}/actions", response_model=AlertView)
async def act_on_alert(
    alert_id: AlertId,
    payload: AlertActionRequest,
    request: Request,
    tenant: TenantDep,
    session: DbSessionDep,
) -> AlertView:
    """Only self-assignment remains; every mutation rechecks persisted identity and policy."""
    enforce_permission(tenant, Permission.TRIAGE_ALERT)
    actor_id = require_actor(tenant)
    if payload.assignee_id is not None and payload.assignee_id != actor_id:
        raise AppError("workflow_action_blocked")
    await _workflow(tenant, session, request).act(
        alert_id=alert_id,
        actor_id=actor_id,
        action=payload.action,
        expected_case_version=payload.expected_case_version,
        note=payload.note,
    )
    view = await _view(
        tenant, session, alert_id, request.app.state.settings.review_low_confidence_margin
    )
    await session.commit()
    return view


@router.patch("/alerts/{alertId}/sar", response_model=SarDraftView)
async def save_sar(
    alert_id: AlertId,
    payload: WorkingSarRequest,
    request: Request,
    tenant: TenantDep,
    session: DbSessionDep,
) -> SarDraftView:
    """Save the owner's masked working narrative with case and SAR version fences."""
    enforce_permission(tenant, Permission.TRIAGE_ALERT)
    draft = await _workflow(tenant, session, request).save_sar(
        alert_id=alert_id, actor_id=require_actor(tenant), payload=payload
    )
    view = sar_draft_to_view(draft)
    await session.commit()
    return view


@router.post("/alerts/{alertId}/submissions", response_model=WorkflowCommandResult)
async def submit_case(  # noqa: PLR0913, PLR0917 - explicit FastAPI command and injected dependencies.
    alert_id: AlertId,
    payload: SubmissionRequest,
    request: Request,
    tenant: TenantDep,
    session: DbSessionDep,
    idempotency_key: IdempotencyKey,
) -> WorkflowCommandResult:
    """Create an immutable round; identical retries return the original response."""
    enforce_permission(tenant, Permission.TRIAGE_ALERT)
    result = await _workflow(tenant, session, request).submit(
        alert_id=alert_id,
        actor_id=require_actor(tenant),
        payload=payload,
        idempotency_key=idempotency_key,
    )
    await session.commit()
    return result


@router.post(
    "/alerts/{alertId}/submissions/{submissionId}/decision", response_model=WorkflowCommandResult
)
async def decide_submission(  # noqa: PLR0913, PLR0917 - explicit FastAPI command and injected dependencies.
    alert_id: AlertId,
    submission_id: SubmissionId,
    payload: DecisionRequest,
    request: Request,
    tenant: TenantDep,
    session: DbSessionDep,
    settings: SettingsDep,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
) -> WorkflowCommandResult:
    """Commit approval and closure atomically before scheduling deferred artifact generation."""
    enforce_permission(tenant, Permission.REVIEW_SAR)
    result = await _workflow(tenant, session, request).decide(
        alert_id=alert_id,
        actor_id=require_actor(tenant),
        submission_id=submission_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    await session.commit()
    if result.decision == "approve":
        views = await submission_views(session, uuid.UUID(tenant.agency_id), alert_id)
        submitted = next(item for item in views if item.submission_id == result.submission_id)
        if submitted.sar_draft is not None:
            background_tasks.add_task(
                generate_sar_pdf,
                sessionmaker=request.app.state.db_sessionmaker,
                storage=get_storage_backend(settings),
                agency_id=uuid.UUID(tenant.agency_id),
                draft_id=uuid.UUID(submitted.sar_draft.sar_draft_id),
                max_attempts=settings.sar_pdf_max_attempts,
            )
    return result

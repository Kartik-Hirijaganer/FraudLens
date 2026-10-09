"""Summary: Shared transactional admission for manual and upload investigations.

Key classes:
- RunAcceptance: the persisted run and whether this call created it.

Key functions:
- create_investigation_run: validate, lock and persist without committing or dispatching.

Notes:
- Upload admission never reserves paid drafting capacity or invokes a cloud job.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.api.deps import (
    Permission,
    audit_writer,
    enforce_permission,
    enforce_rate_limit,
    optional_actor,
)
from fraudlens_backend.db.models import AnalysisRun, RunStatus, Transaction
from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    ModelRegistryRepository,
)
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.pipeline_wiring import RunManager, resolve_workflow_mode
from fraudlens_backend.runs.admission import reserve_agent_spend
from fraudlens_backend.settings import AppSettings

ACTIVE_RUN_STATUSES = (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.RETRYING)
_SECONDS_PER_DAY = 86_400


class RunAcceptance(BaseModel):
    """Internal admission result; no session mutation is hidden from its caller."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    run: AnalysisRun = Field(..., description="Persisted tenant-owned investigation.")
    created: bool = Field(..., description="Whether dispatch is required for this request.")


def _request_fingerprint(
    *,
    transaction_id: uuid.UUID,
    model_override: str | None,
    workflow_mode: str | None,
) -> str:
    """Hash the behavior-bearing request fields bound to an Idempotency-Key."""
    payload = json.dumps(
        {
            "modelOverride": model_override,
            "transactionId": str(transaction_id),
            "workflowMode": workflow_mode,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


async def create_investigation_run(  # noqa: PLR0912, PLR0913, PLR0915 - run creation is the transaction boundary.
    *,
    manager: RunManager,
    session: AsyncSession,
    settings: AppSettings,
    tenant: TenantContext,
    request: Request,
    transaction_id: uuid.UUID,
    model_override: str | None = None,
    idempotency_key: str | None = None,
    workflow_mode: str | None = None,
    import_job_id: uuid.UUID | None = None,
) -> RunAcceptance:
    """Validate and persist a run; the caller owns the enclosing commit and dispatch."""
    if model_override is not None:
        enforce_permission(tenant, Permission.RUN_EVALUATION)
    agency_id = uuid.UUID(tenant.agency_id)
    run_repo = AnalysisRunRepository(session, agency_id)
    fingerprint = _request_fingerprint(
        transaction_id=transaction_id,
        model_override=model_override,
        workflow_mode=workflow_mode,
    )
    if idempotency_key is not None:
        existing = await run_repo.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise AppError("idempotency_key_conflict")
            return RunAcceptance(run=existing, created=False)
    transaction = (
        await session.execute(
            select(Transaction)
            .where(Transaction.agency_id == agency_id, Transaction.id == transaction_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if transaction is None:
        raise AppError("transaction_not_found")
    # Recheck after the transaction lock: another request may have just committed.
    if idempotency_key is not None:
        existing = await run_repo.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise AppError("idempotency_key_conflict")
            return RunAcceptance(run=existing, created=False)
    active = (
        await session.execute(
            select(AnalysisRun)
            .where(
                AnalysisRun.agency_id == agency_id,
                AnalysisRun.transaction_id == transaction_id,
                AnalysisRun.status.in_(ACTIVE_RUN_STATUSES),
            )
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if active is not None:
        raise AppError(
            "investigation_in_progress", details=[{"field": "runId", "message": str(active.id)}]
        )
    if model_override is not None:
        enforce_permission(tenant, Permission.RUN_EVALUATION)
    if model_override is not None and (
        await ModelRegistryRepository(session).get_version_by_label(model_override) is None
    ):
        # Reject an unregistered override BEFORE starting the run (never a silent no-op, §5.4).
        raise AppError("model_version_not_found")
    resolved_workflow = await resolve_workflow_mode(
        session,
        settings=settings,
        agency_id=agency_id,
        requested=workflow_mode,
    )
    evaluation_mode = workflow_mode is not None
    reserve_live_agent_spend = (
        import_job_id is None and resolved_workflow == "multi_agent" and settings.llm_mode == "live"
    )
    if reserve_live_agent_spend and not evaluation_mode:
        client_host = request.client.host if request.client else "unknown"
        quotas = manager.agent_quotas
        enforce_rate_limit(
            request,
            scope="live_multi_agent_per_ip_daily",
            limit=quotas.live_runs_per_ip_per_day,
            window_seconds=_SECONDS_PER_DAY,
            key=client_host,
        )
        enforce_rate_limit(
            request,
            scope="live_multi_agent_total_daily",
            limit=quotas.live_runs_total_per_day,
            window_seconds=_SECONDS_PER_DAY,
            key="all",
        )
    try:
        graph_version = (
            getattr(manager, "agent_graph_version", None)
            if resolved_workflow == "multi_agent"
            else None
        )
        async with session.begin_nested():
            if import_job_id is not None or settings.run_execution_mode == "worker":
                run = await run_repo.create_pending(
                    transaction_id=transaction.id,
                    triggered_by=optional_actor(tenant),
                    idempotency_key=idempotency_key,
                    workflow_mode=resolved_workflow,
                    graph_version=graph_version,
                    request_fingerprint=fingerprint,
                    model_override=model_override,
                    deadline_at=(
                        None
                        if import_job_id is not None
                        else datetime.now(UTC) + timedelta(seconds=settings.run_deadline_seconds)
                    ),
                    import_job_id=import_job_id,
                )
            else:
                run = await run_repo.create_running(
                    transaction_id=transaction.id,
                    triggered_by=optional_actor(tenant),
                    idempotency_key=idempotency_key,
                    workflow_mode=resolved_workflow,
                    graph_version=graph_version,
                    request_fingerprint=fingerprint,
                    model_override=model_override,
                )
    except IntegrityError:
        # A second replica may win the tenant/key UNIQUE race after our initial lookup.
        if idempotency_key is not None:
            existing = await run_repo.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise AppError("idempotency_key_conflict") from None
                return RunAcceptance(run=existing, created=False)
        raise
    if reserve_live_agent_spend:
        attempt_count = settings.run_max_attempts if settings.run_execution_mode == "worker" else 1
        await reserve_agent_spend(
            session,
            run=run,
            agency_id=agency_id,
            maximum_attempt_cost_usd=manager.agent_max_cost_usd * attempt_count,
            daily_budget_ceiling_usd=settings.llm_daily_budget_usd,
        )
    await audit_writer(tenant, session, request).record(
        actor_id=optional_actor(tenant),
        action="investigation.start",
        resource_type="analysis_run",
        resource_id=str(run.id),
        metadata={
            "transactionId": str(transaction.id),
            "workflowMode": resolved_workflow,
            "evaluationMode": str(evaluation_mode).lower(),
            "evaluationQuotaBypass": str(
                evaluation_mode
                and resolved_workflow == "multi_agent"
                and settings.llm_mode == "live"
            ).lower(),
        },
    )
    return RunAcceptance(run=run, created=True)

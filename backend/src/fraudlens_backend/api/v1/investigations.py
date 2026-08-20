"""Summary: The investigation API (plan §5.4, §10.2, §16 Phase 8; endpoints 6-8). `POST
/investigations` STARTS and OWNS the run (ADR-016): it validates the transaction is the agency's,
creates the `analysis_runs(running)` row, launches the `Runner` as an in-process background task
    via the `RunManager`, and returns **202 `{runId}`** — an optional `Idempotency-Key` is hashed
    into the tenant-scoped run row and dedupes across restarts/replicas. `GET
    /investigations/{runId}` is the authoritative snapshot the
SSE observer reconciles against. `GET /investigations/{runId}/stream` is a PURE OBSERVER: it
replays the persisted `analysis_run_events` from `Last-Event-ID`, then tails the live broadcast
(the ephemeral `sar.token`s) until `run.completed`/`run.failed` — it never starts the run, so a
never-connected, dropped, or doubly-reconnected stream never strands or duplicates a run. Every
route is scoped to the verified `agency_id` claim (a cross-tenant runId → 404, no existence leak).

Key classes:
- (none)

Key functions:
- start_investigation: POST /investigations — create + own the run (202; Idempotency-Key dedupe).
- get_investigation: GET /investigations/{runId} — the authoritative run snapshot (404 if absent).
- regenerate_investigation_sar: POST /investigations/{runId}/sar/regenerate — re-draft the SAR.
- stream_investigation: GET /investigations/{runId}/stream — SSE replay-from-Last-Event-ID + tail.

Notes:
- The SSE generator opens its OWN short-lived session for the persisted-event replay and then tails
  the in-memory broadcast queue, so a long-lived stream does not pin the request DB session.
- Stream-owned session cleanup runs in a shielded task so a client disconnect cannot interrupt
  SQLAlchemy while it returns an asyncpg connection to the pool.
- Replaying persisted events (with a `seq`) then de-duping any live event whose `seq` was already
  replayed makes reconnect-from-`Last-Event-ID` exact; ephemeral `sar.token`s (no `seq`) only ever
  arrive live, and `run.completed`/`run.failed` terminate the stream.
- A run with no `RunManager` (no DB configured) fails closed with the `investigations_unavailable`
  503 envelope, consistent with the rest of the DB-backed surface.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, cast

from anyio import CancelScope
from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.api.deps import (
    DbSessionDep,
    Permission,
    SettingsDep,
    audit_writer,
    enforce_permission,
    enforce_rate_limit,
    get_tenant,
    optional_actor,
    require_permission,
)
from fraudlens_backend.db.models import AnalysisResult, AnalysisRun, RagRetrieval, SarDraft
from fraudlens_backend.db.repositories import (
    AgentExecutionRepository,
    AlertRepository,
    AnalysisRunRepository,
    ModelRegistryRepository,
    SarDraftRepository,
    TransactionRepository,
)
from fraudlens_backend.models.agent_executions import agent_execution_to_view
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.models.investigations import (
    InvestigationSnapshotResponse,
    InvestigationStartRequest,
    InvestigationStartResponse,
    RetrievedRegulationView,
)
from fraudlens_backend.models.sar import SarDraftView
from fraudlens_backend.pipeline_wiring import (
    RunManager,
    build_pipeline_input,
    resolve_workflow_mode,
)
from fraudlens_backend.services.sar_regeneration import regenerate_sar_for_run, sar_draft_to_view
from fraudlens_backend.settings import AppSettings

router = APIRouter(tags=["investigations"])

TenantDep = Annotated[TenantContext, Depends(get_tenant)]
InvestigationWriteDep = Annotated[
    TenantContext, Depends(require_permission(Permission.START_INVESTIGATION))
]

_IDEMPOTENCY_HEADER = "Idempotency-Key"
_LAST_EVENT_ID_HEADER = "Last-Event-ID"
_TERMINAL_EVENTS = frozenset({"run.completed", "run.failed"})
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
_SECONDS_PER_DAY = 86_400


def _manager(request: Request) -> RunManager:
    """Return the app's RunManager, or fail closed with 503 when no DB is configured."""
    manager = getattr(request.app.state, "run_manager", None)
    if manager is None:
        raise AppError("investigations_unavailable")
    return cast(RunManager, manager)


async def _create_and_start(  # noqa: PLR0913 - run-creation collaborators + correlation + the optional override (keyword-only).
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
) -> str:
    """Create the running run, build its input, launch the background Runner; return the runId."""
    agency_id = uuid.UUID(tenant.agency_id)
    run_repo = AnalysisRunRepository(session, agency_id)
    if idempotency_key is not None:
        existing = await run_repo.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return str(existing.id)
    repo = TransactionRepository(session, agency_id)
    transaction = await repo.get(transaction_id)
    if transaction is None:
        raise AppError("transaction_not_found")
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
    if resolved_workflow == "multi_agent" and settings.llm_mode == "live":
        if not evaluation_mode:
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
        await manager.ensure_agent_budget(session, agency_id=agency_id)
    try:
        run = await run_repo.create_running(
            transaction_id=transaction.id,
            idempotency_key=idempotency_key,
            workflow_mode=resolved_workflow,
            graph_version=(
                getattr(manager, "agent_graph_version", None)
                if resolved_workflow == "multi_agent"
                else None
            ),
        )
    except IntegrityError:
        # A second replica may win the tenant/key UNIQUE race after our initial lookup.
        await session.rollback()
        if idempotency_key is not None:
            existing = await run_repo.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return str(existing.id)
        raise
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
    await session.commit()
    pipeline_input = await build_pipeline_input(
        repo=repo,
        transaction=transaction,
        run_id=run.id,
        agency_id=agency_id,
        settings=settings,
    )
    manager.start(
        agency_id=agency_id,
        run_id=run.id,
        transaction_id=transaction.id,
        pipeline_input=pipeline_input,
        model_override=model_override,
        workflow_mode=resolved_workflow,
    )
    return str(run.id)


@router.post("/investigations", response_model=InvestigationStartResponse, status_code=202)
async def start_investigation(
    payload: InvestigationStartRequest,
    request: Request,
    tenant: InvestigationWriteDep,
    session: DbSessionDep,
    settings: SettingsDep,
) -> InvestigationStartResponse:
    """Start + own an investigation run (202 {runId}); an Idempotency-Key dedupes double-clicks."""
    if payload.workflow_mode is not None:
        enforce_permission(tenant, Permission.RUN_EVALUATION)
    manager = _manager(request)
    idempotency_key = request.headers.get(_IDEMPOTENCY_HEADER)
    run_id = await _create_and_start(
        manager=manager,
        session=session,
        settings=settings,
        tenant=tenant,
        request=request,
        transaction_id=payload.transaction_id,
        model_override=payload.model_override,
        idempotency_key=idempotency_key or None,
        workflow_mode=payload.workflow_mode,
    )
    return InvestigationStartResponse(run_id=run_id)


def _snapshot(  # noqa: PLR0913 -- projection joins the run's tenant-scoped durable records.
    run: AnalysisRun,
    result: AnalysisResult | None,
    retrieval: RagRetrieval | None,
    sar: SarDraft | None,
    alert_id: uuid.UUID | None,
    agent_executions: list[Any] | None = None,
) -> InvestigationSnapshotResponse:
    """Project the run + (optional) result + (optional) SAR draft onto the snapshot response."""
    return InvestigationSnapshotResponse(
        run_id=str(run.id),
        transaction_id=str(run.transaction_id),
        status=run.status.value,
        risk_score=run.risk_score,
        risk_band=run.risk_band.value if run.risk_band is not None else None,
        fraud_probability=result.fraud_probability if result is not None else None,
        model_version=run.model_version,
        rules_version=run.rules_version,
        rag_version=run.rag_version,
        prompt_version=run.prompt_version,
        workflow_mode=run.workflow_mode,
        graph_version=run.graph_version,
        error_code=run.error_code,
        top_features=list(result.top_features) if result is not None else [],
        rule_hits=list(result.rule_hits) if result is not None else [],
        citations=list(sar.citations) if sar is not None else [],
        retrieved_regulations=(
            [RetrievedRegulationView.model_validate(item) for item in retrieval.chunks]
            if retrieval is not None
            else []
        ),
        sar_status=sar.status.value if sar is not None else None,
        sar_draft_id=str(sar.id) if sar is not None else None,
        sar_content=sar.content if sar is not None else None,
        revision_count=sar.revision_count if sar is not None else 0,
        agent_executions=[agent_execution_to_view(item) for item in (agent_executions or [])],
        alert_id=str(alert_id) if alert_id is not None else None,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


@router.get("/investigations/{runId}", response_model=InvestigationSnapshotResponse)
async def get_investigation(
    run_id: Annotated[uuid.UUID, Path(alias="runId")],
    tenant: TenantDep,
    session: DbSessionDep,
) -> InvestigationSnapshotResponse:
    """Return the authoritative run snapshot; 404 when missing or owned by another agency."""
    agency_id = uuid.UUID(tenant.agency_id)
    repo = AnalysisRunRepository(session, agency_id)
    run = await repo.get(run_id)
    if run is None:
        raise AppError("investigation_not_found")
    result = await repo.get_result(run_id)
    retrieval = await repo.get_retrieval(run_id)
    sar = await SarDraftRepository(session, agency_id).get_for_run(run_id)
    alert = await AlertRepository(session, agency_id).get_for_run(run_id)
    executions = await AgentExecutionRepository(session, agency_id).list_for_run(run_id)
    return _snapshot(
        run,
        result,
        retrieval,
        sar,
        alert.id if alert is not None else None,
        list(executions),
    )


@router.post("/investigations/{runId}/sar/regenerate", response_model=SarDraftView)
async def regenerate_investigation_sar(
    run_id: Annotated[uuid.UUID, Path(alias="runId")],
    request: Request,
    tenant: InvestigationWriteDep,
    session: DbSessionDep,
    settings: SettingsDep,
) -> SarDraftView:
    """Re-draft the run's SAR from its persisted evidence and persist the next version (§7, §10.4).

    404 when the run is missing/cross-tenant; 409 when the run has no completed result to draft from
    or its latest draft is already approved/rejected.
    """
    agency_id = uuid.UUID(tenant.agency_id)
    actor_id = optional_actor(tenant)
    draft = await regenerate_sar_for_run(
        session=session,
        agency_id=agency_id,
        run_id=run_id,
        settings=settings,
        created_by=actor_id,
    )
    await audit_writer(tenant, session, request).record(
        actor_id=actor_id,
        action="sar.regenerate",
        resource_type="sar_draft",
        resource_id=str(draft.id),
        metadata={"runId": str(run_id), "version": str(draft.version)},
    )
    await session.commit()
    return sar_draft_to_view(draft)


def _parse_last_event_id(request: Request) -> int:
    """Parse the SSE `Last-Event-ID` (header or `lastEventId` query) as a seq; 0 when absent/bad."""
    raw = request.headers.get(_LAST_EVENT_ID_HEADER) or request.query_params.get("lastEventId")
    try:
        return max(0, int(raw)) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def _sse_frame(seq: int | None, event_type: str, data: dict[str, Any]) -> str:
    """Format one Server-Sent Event frame (id only for persisted events with a seq)."""
    lines = []
    if seq is not None:
        lines.append(f"id: {seq}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(data, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"


@asynccontextmanager
async def _stream_session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield an SSE-owned session and finish closing it even when the request is cancelled."""
    session = sessionmaker()
    with CancelScope(shield=True):
        try:
            yield session
        finally:
            await session.close()


async def _event_stream(
    *,
    manager: RunManager,
    sessionmaker: async_sessionmaker[AsyncSession],
    agency_id: uuid.UUID,
    run_id: uuid.UUID,
    after_seq: int,
) -> AsyncIterator[str]:
    """Replay persisted events from `after_seq`, then tail the live broadcast until terminal."""
    queue = manager.attach(str(run_id))
    try:
        max_seq = after_seq
        async with _stream_session(sessionmaker) as session:
            events = await AnalysisRunRepository(session, agency_id).events_after(
                run_id=run_id, after_seq=after_seq
            )
        for event in events:
            payload = await _terminal_snapshot_payload(
                sessionmaker=sessionmaker,
                agency_id=agency_id,
                run_id=run_id,
                event_type=event.event_type.value,
                payload=dict(event.payload),
            )
            yield _sse_frame(event.seq, event.event_type.value, payload)
            max_seq = event.seq
            if event.event_type.value in _TERMINAL_EVENTS:
                return
        if queue is None:  # run is terminal/evicted — the persisted replay is the whole stream
            return
        while True:
            message = await queue.get()
            if message is None:  # the run finished (done sentinel)
                return
            if message.seq is not None and message.seq <= max_seq:
                continue  # already replayed from the persisted log
            payload = await _terminal_snapshot_payload(
                sessionmaker=sessionmaker,
                agency_id=agency_id,
                run_id=run_id,
                event_type=message.event_type,
                payload=message.data,
            )
            yield _sse_frame(message.seq, message.event_type, payload)
            if message.seq is not None:
                max_seq = message.seq
            if message.event_type in _TERMINAL_EVENTS:
                return
    finally:
        if queue is not None:
            manager.detach(str(run_id), queue)


async def _terminal_snapshot_payload(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    agency_id: uuid.UUID,
    run_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Add the run's nullable alert id and SAR status to the terminal SSE snapshot."""
    if event_type != "run.completed":
        return payload
    async with _stream_session(sessionmaker) as session:
        alert = await AlertRepository(session, agency_id).get_for_run(run_id)
        sar = await SarDraftRepository(session, agency_id).get_for_run(run_id)
    return {
        **payload,
        "alertId": str(alert.id) if alert is not None else None,
        "sarStatus": sar.status.value if sar is not None else None,
    }


@router.get("/investigations/{runId}/stream")
async def stream_investigation(
    run_id: Annotated[uuid.UUID, Path(alias="runId")],
    request: Request,
    tenant: TenantDep,
) -> StreamingResponse:
    """Stream a run as SSE: replay persisted events from Last-Event-ID, then tail live tokens."""
    manager = _manager(request)
    sessionmaker = getattr(request.app.state, "db_sessionmaker", None)
    if sessionmaker is None:
        raise AppError("investigations_unavailable")
    agency_id = uuid.UUID(tenant.agency_id)
    async with _stream_session(sessionmaker) as session:
        run = await AnalysisRunRepository(session, agency_id).get(run_id)
    if run is None:  # missing or another agency's run — 404 with no existence leak
        raise AppError("investigation_not_found")
    generator = _event_stream(
        manager=manager,
        sessionmaker=sessionmaker,
        agency_id=agency_id,
        run_id=run_id,
        after_seq=_parse_last_event_id(request),
    )
    return StreamingResponse(generator, media_type="text/event-stream", headers=_SSE_HEADERS)

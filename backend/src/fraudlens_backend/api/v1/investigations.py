"""Summary: The investigation API (plan §5.4, §10.2, §16 Phase 8; endpoints 6-8). `POST
/investigations` STARTS and OWNS the run (ADR-016): it validates the transaction is the agency's,
creates the `analysis_runs(running)` row, launches the `Runner` as an in-process background task
via the `RunManager`, and returns **202 `{runId}`** — an optional `Idempotency-Key` dedupes
double-clicks to the existing run. `GET /investigations/{runId}` is the authoritative snapshot the
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
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.api.deps import (
    DbSessionDep,
    Permission,
    SettingsDep,
    audit_writer,
    get_tenant,
    optional_actor,
    require_permission,
)
from fraudlens_backend.db.models import AnalysisResult, AnalysisRun, SarDraft
from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    ModelRegistryRepository,
    SarDraftRepository,
    TransactionRepository,
)
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.errors import AppError
from fraudlens_backend.models.investigations import (
    InvestigationSnapshotResponse,
    InvestigationStartRequest,
    InvestigationStartResponse,
)
from fraudlens_backend.models.sar import SarDraftView
from fraudlens_backend.pipeline_wiring import RunManager, build_pipeline_input
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
) -> str:
    """Create the running run, build its input, launch the background Runner; return the runId."""
    agency_id = uuid.UUID(tenant.agency_id)
    repo = TransactionRepository(session, agency_id)
    transaction = await repo.get(transaction_id)
    if transaction is None:
        raise AppError("transaction_not_found")
    if model_override is not None and (
        await ModelRegistryRepository(session).get_version_by_label(model_override) is None
    ):
        # Reject an unregistered override BEFORE starting the run (never a silent no-op, §5.4).
        raise AppError("model_version_not_found")
    run = await AnalysisRunRepository(session, agency_id).create_running(
        transaction_id=transaction.id
    )
    await audit_writer(tenant, session, request).record(
        actor_id=optional_actor(tenant),
        action="investigation.start",
        resource_type="analysis_run",
        resource_id=str(run.id),
        metadata={"transactionId": str(transaction.id)},
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
    manager = _manager(request)
    idempotency_key = request.headers.get(_IDEMPOTENCY_HEADER)
    if idempotency_key:
        async with manager.lock:
            existing = manager.lookup_idempotent(tenant.agency_id, idempotency_key)
            if existing is not None:
                return InvestigationStartResponse(run_id=existing)
            run_id = await _create_and_start(
                manager=manager,
                session=session,
                settings=settings,
                tenant=tenant,
                request=request,
                transaction_id=payload.transaction_id,
                model_override=payload.model_override,
            )
            manager.remember_idempotent(tenant.agency_id, idempotency_key, run_id)
            return InvestigationStartResponse(run_id=run_id)
    run_id = await _create_and_start(
        manager=manager,
        session=session,
        settings=settings,
        tenant=tenant,
        request=request,
        transaction_id=payload.transaction_id,
        model_override=payload.model_override,
    )
    return InvestigationStartResponse(run_id=run_id)


def _snapshot(
    run: AnalysisRun, result: AnalysisResult | None, sar: SarDraft | None
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
        error_code=run.error_code,
        top_features=list(result.top_features) if result is not None else [],
        rule_hits=list(result.rule_hits) if result is not None else [],
        citations=list(sar.citations) if sar is not None else [],
        sar_status=sar.status.value if sar is not None else None,
        sar_draft_id=str(sar.id) if sar is not None else None,
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
    sar = await SarDraftRepository(session, agency_id).get_for_run(run_id)
    return _snapshot(run, result, sar)


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
        async with sessionmaker() as session:
            events = await AnalysisRunRepository(session, agency_id).events_after(
                run_id=run_id, after_seq=after_seq
            )
        for event in events:
            yield _sse_frame(event.seq, event.event_type.value, dict(event.payload))
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
            yield _sse_frame(message.seq, message.event_type, message.data)
            if message.seq is not None:
                max_seq = message.seq
            if message.event_type in _TERMINAL_EVENTS:
                return
    finally:
        if queue is not None:
            manager.detach(str(run_id), queue)


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
    async with sessionmaker() as session:
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

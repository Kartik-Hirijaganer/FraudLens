"""Summary: Durable SSE replay and process-local live-tail helpers for investigations.

Key classes:
- EventPolling: validated durable-event polling and heartbeat intervals.

Key functions:
- event_stream: replay persisted events, then tail a live RunManager subscription.
- parse_last_event_id: parse the standard SSE resume cursor without raising.
- stream_session: provide cancellation-safe short-lived replay sessions.

Notes:
- Compatibility aliases remain imported by api.v1.investigations for existing tests and callers.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from anyio import CancelScope
from fastapi import Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.db.models.enums import RunStatus
from fraudlens_backend.db.repositories import (
    AlertRepository,
    AnalysisRunRepository,
    SarDraftRepository,
)
from fraudlens_backend.pipeline_wiring import RunManager

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
_LAST_EVENT_ID_HEADER = "Last-Event-ID"
_TERMINAL_EVENTS = frozenset({"run.completed", "run.failed"})


class EventPolling(BaseModel):
    """Validated intervals used when the API and worker do not share process memory."""

    initial_seconds: float = Field(..., gt=0, description="Initial database polling interval.")
    maximum_seconds: float = Field(..., gt=0, description="Maximum polling backoff interval.")
    heartbeat_seconds: float = Field(..., gt=0, description="Maximum quiet SSE interval.")


def parse_last_event_id(request: Request) -> int:
    """Parse the SSE resume cursor from a header or compatibility query parameter."""
    raw = request.headers.get(_LAST_EVENT_ID_HEADER) or request.query_params.get("lastEventId")
    try:
        return max(0, int(raw)) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def _sse_frame(seq: int | None, event_type: str, data: dict[str, object]) -> str:
    """Format one SSE frame, adding an id only for a persisted event."""
    lines = []
    if seq is not None:
        lines.append(f"id: {seq}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(data, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"


@asynccontextmanager
async def stream_session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a replay session and shield its connection-pool cleanup from cancellation."""
    session = sessionmaker()
    with CancelScope(shield=True):
        try:
            yield session
        finally:
            await session.close()


async def _terminal_payload(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    agency_id: uuid.UUID,
    run_id: uuid.UUID,
    event_type: str,
    payload: dict[str, object],
) -> dict[str, object]:
    """Add the nullable alert id and SAR state to a completed terminal snapshot."""
    if event_type != "run.completed":
        return payload
    async with stream_session(sessionmaker) as session:
        alert = await AlertRepository(session, agency_id).get_for_run(run_id)
        sar = await SarDraftRepository(session, agency_id).get_for_run(run_id)
    return {
        **payload,
        "alertId": str(alert.id) if alert is not None else None,
        "sarStatus": sar.status.value if sar is not None else None,
    }


async def _poll_persisted_events(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    agency_id: uuid.UUID,
    run_id: uuid.UUID,
    after_seq: int,
    polling: EventPolling,
) -> AsyncIterator[str]:
    """Poll the durable event log with bounded backoff until the run becomes terminal."""
    delay = polling.initial_seconds
    quiet_for = 0.0
    while True:
        await asyncio.sleep(delay)
        quiet_for += delay
        async with stream_session(sessionmaker) as session:
            repository = AnalysisRunRepository(session, agency_id)
            events = await repository.events_after(run_id=run_id, after_seq=after_seq)
            run = await repository.get(run_id)
        if events:
            delay = polling.initial_seconds
            quiet_for = 0.0
        for event in events:
            payload = await _terminal_payload(
                sessionmaker=sessionmaker,
                agency_id=agency_id,
                run_id=run_id,
                event_type=event.event_type.value,
                payload=dict(event.payload),
            )
            yield _sse_frame(event.seq, event.event_type.value, payload)
            after_seq = event.seq
            if event.event_type.value in _TERMINAL_EVENTS:
                return
        if run is None or run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
            return
        if quiet_for >= polling.heartbeat_seconds:
            yield ": keepalive\n\n"
            quiet_for = 0.0
        delay = min(delay * 2, polling.maximum_seconds)


async def event_stream(  # noqa: PLR0913 - stream identity and durable source are explicit.
    *,
    manager: RunManager,
    sessionmaker: async_sessionmaker[AsyncSession],
    agency_id: uuid.UUID,
    run_id: uuid.UUID,
    after_seq: int,
    polling: EventPolling | None = None,
) -> AsyncIterator[str]:
    """Replay persisted events, then tail the process-local broadcast until terminal."""
    queue = manager.attach(str(run_id))
    try:
        max_seq = after_seq
        async with stream_session(sessionmaker) as session:
            events = await AnalysisRunRepository(session, agency_id).events_after(
                run_id=run_id, after_seq=after_seq
            )
        for event in events:
            payload = await _terminal_payload(
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
        if queue is None and polling is None:
            return
        if queue is None:
            assert polling is not None  # Narrowed by the preceding no-source return.
            async for frame in _poll_persisted_events(
                sessionmaker=sessionmaker,
                agency_id=agency_id,
                run_id=run_id,
                after_seq=max_seq,
                polling=polling,
            ):
                yield frame
            return
        while True:
            message = await queue.get()
            if message is None:
                return
            if message.seq is not None and message.seq <= max_seq:
                continue
            payload = await _terminal_payload(
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

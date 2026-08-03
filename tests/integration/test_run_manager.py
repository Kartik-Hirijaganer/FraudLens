"""Tests for the in-process RunManager + the SSE `_event_stream` (plan §16 Phase 8, ADR-016):
idempotency dedupe, background-task launch + live broadcast to subscribers, run-state eviction, and
the observer's replay-from-`Last-Event-ID` then live-tail with seq de-duplication. The Runner is
driven through fakes (build_pipeline_deps is patched) so these assert the orchestration plumbing
without the heavy model; the SSE generator is stepped deterministically via `__anext__`."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pipeline_fakes import (
    FakeExplainerPort,
    FakeRetrieverPort,
    FakeRulesPort,
    FakeRunStore,
    FakeSarDrafter,
    FakeScorerPort,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tenancy import new_agency_id

import fraudlens_backend.pipeline_wiring as wiring
from fraudlens_backend.api.v1.investigations import _event_stream
from fraudlens_backend.db.models import Agency, AnalysisRun, AnalysisRunEvent, RunStatus
from fraudlens_backend.db.models.enums import AnalysisRunEventType
from fraudlens_backend.pipeline_wiring import PipelineComponents, RunManager, _RunState
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskPolicy, RuleContext
from fraudlens_core.rules.base import RuleTransaction
from fraudlens_ml.pipeline import PipelineDeps, PipelineInput, StreamMessage

_AGENCY_ID = new_agency_id()


def _components(make_settings: Callable[..., AppSettings]) -> PipelineComponents:
    """Build real (but here unused) components; build_pipeline_deps is patched in these tests."""
    return wiring.build_pipeline_components(make_settings(llm_mode="mock"))


def _manager(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> RunManager:
    """A RunManager over the in-memory sessionmaker + real components."""
    return RunManager(
        sessionmaker=db_sessionmaker,
        components=_components(make_settings),
        settings=make_settings(),
    )


def _pipeline_input(run_id: uuid.UUID) -> PipelineInput:
    """A minimal PipelineInput (the fake ports ignore the context)."""
    txn = RuleTransaction(
        amount=Decimal("9500"),
        currency="USD",
        country="US",
        channel="wire",
        occurred_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    return PipelineInput(
        agency_id=str(_AGENCY_ID),
        run_id=str(run_id),
        transaction_id="t1",
        rule_context=RuleContext(transaction=txn),
        amount=Decimal("9500"),
        currency="USD",
        country="US",
        channel="wire",
        feature_hash="fh",
    )


def test_idempotency_lookup_and_remember(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    manager = _manager(make_settings, db_sessionmaker)
    assert manager.lookup_idempotent("a1", "key-1") is None
    manager.remember_idempotent("a1", "key-1", "run-1")
    assert manager.lookup_idempotent("a1", "key-1") == "run-1"
    assert manager.lookup_idempotent("a2", "key-1") is None  # scoped per agency


def test_attach_returns_none_for_unknown_run(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    manager = _manager(make_settings, db_sessionmaker)
    assert manager.attach(str(uuid.uuid4())) is None


def test_idempotency_map_is_lru_bounded(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    manager = RunManager(
        sessionmaker=db_sessionmaker,
        components=_components(make_settings),
        settings=make_settings(investigation_idempotency_cache_size=2),
    )
    manager.remember_idempotent("a", "k1", "r1")
    manager.remember_idempotent("a", "k2", "r2")
    assert manager.lookup_idempotent("a", "k1") == "r1"  # touch k1 → k2 becomes least-recent
    manager.remember_idempotent("a", "k3", "r3")  # over cap → evict the LRU entry (k2)
    assert manager.lookup_idempotent("a", "k2") is None  # evicted
    assert manager.lookup_idempotent("a", "k1") == "r1"  # retained (recently used)
    assert manager.lookup_idempotent("a", "k3") == "r3"  # retained (newest)


async def test_start_drives_run_and_broadcasts_then_evicts(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_build_deps(*, emit, **_kwargs: object) -> PipelineDeps:
        return PipelineDeps(
            rules=FakeRulesPort(),
            scorer=FakeScorerPort(),
            explainer=FakeExplainerPort(),
            retriever=FakeRetrieverPort(),
            drafter=FakeSarDrafter(tokens=("x",)),
            store=FakeRunStore(),
            emit=emit,
            risk_policy=RiskPolicy(),
        )

    monkeypatch.setattr(wiring, "build_pipeline_deps", fake_build_deps)
    manager = _manager(make_settings, db_sessionmaker)
    run_id = uuid.uuid4()
    manager.start(
        agency_id=_AGENCY_ID,
        run_id=run_id,
        transaction_id=uuid.uuid4(),
        pipeline_input=_pipeline_input(run_id),
    )
    queue = manager.attach(str(run_id))
    assert queue is not None
    await manager.join(str(run_id))

    received: list[StreamMessage | None] = []
    while not queue.empty():
        received.append(queue.get_nowait())
    event_types = [message.event_type for message in received if message is not None]
    assert event_types[0] == "run.started"
    assert "sar.token" in event_types
    assert event_types[-1] == "run.completed"
    assert received[-1] is None  # the done sentinel wakes a tailing observer

    manager.detach(str(run_id), queue)
    assert manager.attach(str(run_id)) is None  # finished + unsubscribed → evicted


async def _seed_run(
    session: AsyncSession, *, status: RunStatus, event_types: list[AnalysisRunEventType]
) -> uuid.UUID:
    """Insert an agency + a run + its ordered events; return the run id."""
    session.add(Agency(id=_AGENCY_ID, name="S", slug="s-mgr"))
    run = AnalysisRun(agency_id=_AGENCY_ID, transaction_id=uuid.uuid4(), status=status)
    session.add(run)
    await session.flush()
    for seq, event_type in enumerate(event_types, start=1):
        session.add(
            AnalysisRunEvent(
                agency_id=_AGENCY_ID, run_id=run.id, seq=seq, event_type=event_type, payload={}
            )
        )
    await session.commit()
    return run.id


def _event_name(frame: str) -> str:
    """Extract the `event:` line value from an SSE frame."""
    return next(line[len("event: ") :] for line in frame.splitlines() if line.startswith("event: "))


async def test_event_stream_replays_terminal_run_from_db(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        run_id = await _seed_run(
            session,
            status=RunStatus.COMPLETED,
            event_types=[
                AnalysisRunEventType.RUN_STARTED,
                AnalysisRunEventType.STEP_RULES_COMPLETED,
                AnalysisRunEventType.RUN_COMPLETED,
            ],
        )
    manager = _manager(make_settings, db_sessionmaker)  # no live state for this run

    frames = [
        frame
        async for frame in _event_stream(
            manager=manager,
            sessionmaker=db_sessionmaker,
            agency_id=_AGENCY_ID,
            run_id=run_id,
            after_seq=0,
        )
    ]
    assert [_event_name(frame) for frame in frames] == [
        "run.started",
        "step.rules.completed",
        "run.completed",
    ]
    assert frames[0].startswith("id: 1")


async def test_event_stream_resumes_from_last_event_id(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        run_id = await _seed_run(
            session,
            status=RunStatus.COMPLETED,
            event_types=[
                AnalysisRunEventType.RUN_STARTED,
                AnalysisRunEventType.STEP_RULES_COMPLETED,
                AnalysisRunEventType.RUN_COMPLETED,
            ],
        )
    manager = _manager(make_settings, db_sessionmaker)

    frames = [
        frame
        async for frame in _event_stream(
            manager=manager,
            sessionmaker=db_sessionmaker,
            agency_id=_AGENCY_ID,
            run_id=run_id,
            after_seq=2,  # resume after seq 2 → only the terminal event replays
        )
    ]
    assert [_event_name(frame) for frame in frames] == ["run.completed"]


async def test_event_stream_replays_then_tails_live_with_dedup(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        run_id = await _seed_run(
            session,
            status=RunStatus.RUNNING,
            event_types=[AnalysisRunEventType.RUN_STARTED],
        )
    manager = _manager(make_settings, db_sessionmaker)
    manager._runs[str(run_id)] = _RunState()  # an active run with a live broadcast

    gen = _event_stream(
        manager=manager,
        sessionmaker=db_sessionmaker,
        agency_id=_AGENCY_ID,
        run_id=run_id,
        after_seq=0,
    )
    first = await gen.__anext__()  # replayed run.started (seq 1) from the DB
    assert _event_name(first) == "run.started"

    queue = next(iter(manager._runs[str(run_id)].subscribers))
    queue.put_nowait(StreamMessage(event_type="run.started", seq=1, data={}))  # dup → skipped
    queue.put_nowait(StreamMessage(event_type="sar.token", data={"token": "hi"}))
    queue.put_nowait(StreamMessage(event_type="run.completed", seq=2, data={}))

    assert _event_name(await gen.__anext__()) == "sar.token"  # the seq-1 dup was skipped
    assert _event_name(await gen.__anext__()) == "run.completed"
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()

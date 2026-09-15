"""Summary: Durable run claim, heartbeat, fencing, retry, and deadline tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Tests use the portable SQLite path; PostgreSQL locking is covered by the integration gate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.db.models import Agency, AnalysisRunEvent, RunStatus, Transaction
from fraudlens_backend.db.repositories.analysis import AnalysisRunRepository
from fraudlens_backend.runs import (
    LeaseLostError,
    claim_next_run,
    heartbeat_lease,
    reap_expired_runs,
)
from fraudlens_backend.runs.reaper import reap_stale_runs
from fraudlens_backend.settings import AppSettings

_AGENCY_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


async def _queued_run(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
) -> uuid.UUID:
    async with sessionmaker() as session:
        session.add(Agency(id=_AGENCY_ID, name="Lease Test", slug="lease-test"))
        transaction = Transaction(
            agency_id=_AGENCY_ID,
            external_id="lease-transaction",
            amount=Decimal("100.00"),
            currency="USD",
            occurred_at=now,
            origin_account="masked-origin",
            dest_account="masked-destination",
            channel="wire",
            country="US",
            features={},
            feature_hash="f" * 64,
        )
        session.add(transaction)
        await session.flush()
        run = await AnalysisRunRepository(session, _AGENCY_ID).create_pending(
            transaction_id=transaction.id,
            deadline_at=now + timedelta(minutes=5),
            request_fingerprint="a" * 64,
            workflow_mode="single_writer",
        )
        await session.commit()
        return run.id


async def test_claim_and_heartbeat_are_tenant_carrying_and_fenced(db_sessionmaker) -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    run_id = await _queued_run(db_sessionmaker, now=now)
    async with db_sessionmaker() as session:
        claim = await claim_next_run(
            session,
            lease_owner="worker-a",
            now=now,
            lease_seconds=60,
        )
        assert claim is not None
        assert claim.run_id == run_id and claim.agency_id == _AGENCY_ID
        assert claim.attempt == 1 and claim.fencing_token == 1
        await session.commit()

    async with db_sessionmaker() as session:
        assert await heartbeat_lease(
            session,
            claim,
            now=now + timedelta(seconds=10),
            lease_seconds=60,
        )
        await session.commit()
        repo = AnalysisRunRepository(session, _AGENCY_ID)
        await repo.require_fence(
            run_id=run_id,
            lease_owner=claim.lease_owner,
            fencing_token=claim.fencing_token,
        )
        with pytest.raises(LeaseLostError):
            await repo.require_fence(
                run_id=run_id,
                lease_owner="worker-b",
                fencing_token=claim.fencing_token,
            )


async def test_expired_lease_retries_then_rejects_stale_heartbeat(db_sessionmaker) -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    run_id = await _queued_run(db_sessionmaker, now=now)
    async with db_sessionmaker() as session:
        first = await claim_next_run(session, lease_owner="worker-a", now=now, lease_seconds=10)
        assert first is not None
        await session.commit()
    async with db_sessionmaker() as session:
        result = await reap_expired_runs(
            session,
            now=now + timedelta(seconds=11),
            max_attempts=3,
            retry_backoff_seconds=5,
        )
        await session.commit()
        assert result.retried == 1 and result.failed == 0
    async with db_sessionmaker() as session:
        assert not await heartbeat_lease(
            session,
            first,
            now=now + timedelta(seconds=12),
            lease_seconds=10,
        )
        assert await claim_next_run(
            session,
            lease_owner="worker-b",
            now=now + timedelta(seconds=16),
            lease_seconds=10,
        )
        run = await AnalysisRunRepository(session, _AGENCY_ID).get(run_id)
        assert run is not None
        assert run.status is RunStatus.RUNNING and run.attempt == 2 and run.fencing_token == 2


async def test_attempt_and_deadline_bounds_fail_expired_runs(db_sessionmaker) -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    run_id = await _queued_run(db_sessionmaker, now=now)
    async with db_sessionmaker() as session:
        claim = await claim_next_run(session, lease_owner="worker-a", now=now, lease_seconds=1)
        assert claim is not None
        await session.commit()
    async with db_sessionmaker() as session:
        result = await reap_expired_runs(
            session,
            now=now + timedelta(seconds=2),
            max_attempts=1,
            retry_backoff_seconds=5,
        )
        await session.commit()
        run = await AnalysisRunRepository(session, _AGENCY_ID).get(run_id)
        assert result.failed == 1 and run is not None
        assert run.status is RunStatus.FAILED
        assert run.error_code == "run_attempts_exhausted"


async def test_claim_skips_runs_past_deadline(db_sessionmaker) -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    run_id = await _queued_run(db_sessionmaker, now=now)
    async with db_sessionmaker() as session:
        assert (
            await claim_next_run(
                session,
                lease_owner="worker-a",
                now=now + timedelta(minutes=6),
                lease_seconds=60,
            )
            is None
        )

    async with db_sessionmaker() as session:
        result = await reap_expired_runs(
            session,
            now=now + timedelta(minutes=6),
            max_attempts=3,
            retry_backoff_seconds=5,
        )
        await session.commit()
        run = await AnalysisRunRepository(session, _AGENCY_ID).get(run_id)
    assert result.failed == 1 and run is not None
    assert run.status is RunStatus.FAILED and run.error_code == "run_deadline"


async def test_terminal_reaper_transition_appends_failure_event(db_sessionmaker) -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    run_id = await _queued_run(db_sessionmaker, now=now)
    async with db_sessionmaker() as session:
        claim = await claim_next_run(session, lease_owner="worker-a", now=now, lease_seconds=1)
        assert claim is not None
        await session.commit()
    async with db_sessionmaker() as session:
        result = await reap_stale_runs(
            session,
            AppSettings(run_max_attempts=1),
            now=now + timedelta(seconds=2),
        )
        await session.commit()
        events = (
            (
                await session.execute(
                    select(AnalysisRunEvent).where(
                        AnalysisRunEvent.agency_id == _AGENCY_ID,
                        AnalysisRunEvent.run_id == run_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert result.failed == 1
    assert [event.event_type.value for event in events] == ["run.failed"]
    assert events[0].payload == {"code": "run_attempts_exhausted"}

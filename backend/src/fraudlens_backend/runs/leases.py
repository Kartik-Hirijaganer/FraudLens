"""Summary: Portable durable-run claims, heartbeats, fencing, and expired-lease recovery.

Key classes:
- LeaseClaim: immutable tenant-carrying ownership token returned to one worker.
- ReapedFailure: tenant-carrying terminal transition produced by recovery.
- ReapResult: counts of retry and terminal transitions made by one recovery pass.
- LeaseLostError: stable internal signal that a stale worker no longer owns a run.

Key functions:
- claim_next_run: atomically claim one eligible run with row locking on PostgreSQL.
- heartbeat_lease: extend an exact agency/run/owner/fencing-token lease.
- abandon_claim: expire an exact claim after a known worker-side failure.
- reap_expired_runs: retry or fail expired leases under configured attempt/deadline bounds.

Notes:
- The scheduler discovers runs globally, but every claim carries agency_id and all execution uses
  an agency-scoped repository before any transaction or result is read or written.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models.analysis import AnalysisRun
from fraudlens_backend.db.models.enums import RunStatus

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class LeaseLostError(RuntimeError):
    """Raised internally when a worker presents stale lease ownership."""


class LeaseClaim(BaseModel):
    """Immutable ownership and reconstruction data for one claimed run."""

    model_config = _MODEL_CONFIG

    run_id: uuid.UUID = Field(..., description="Claimed investigation run id.")
    agency_id: uuid.UUID = Field(..., description="Tenant scope that owns the run.")
    transaction_id: uuid.UUID = Field(..., description="Tenant-scoped transaction to load.")
    lease_owner: str = Field(..., min_length=1, description="Worker instance identifier.")
    fencing_token: int = Field(..., gt=0, description="Monotonic write-fencing token.")
    attempt: int = Field(..., gt=0, description="One-based execution attempt.")
    workflow_mode: str = Field(..., min_length=1, description="Persisted drafting workflow.")
    model_override: str | None = Field(default=None, description="Persisted model override.")
    lease_expires_at: datetime = Field(..., description="Current lease expiration in UTC.")
    deadline_at: datetime = Field(..., description="Absolute run deadline in UTC.")


class ReapedFailure(BaseModel):
    """Tenant-carrying terminal transition produced while the run row remains locked."""

    model_config = _MODEL_CONFIG

    run_id: uuid.UUID = Field(..., description="Run transitioned to terminal failure.")
    agency_id: uuid.UUID = Field(..., description="Tenant scope owning the failed run.")
    error_code: str = Field(..., min_length=1, description="Stable terminal failure code.")


class ReapResult(BaseModel):
    """Summary of one stale-lease recovery pass."""

    model_config = _MODEL_CONFIG

    retried: int = Field(..., ge=0, description="Expired runs scheduled for another attempt.")
    failed: int = Field(..., ge=0, description="Expired runs failed at their attempt/deadline cap.")
    failed_runs: tuple[ReapedFailure, ...] = Field(
        default=(), description="Tenant-scoped runs requiring terminal events."
    )


def _utc(value: datetime) -> datetime:
    """Normalize SQLite-naive and PostgreSQL-aware timestamps to aware UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def claim_next_run(
    session: AsyncSession,
    *,
    lease_owner: str,
    now: datetime,
    lease_seconds: int,
) -> LeaseClaim | None:
    """Atomically claim the oldest eligible pending/retrying run."""
    eligible = and_(
        AnalysisRun.status.in_((RunStatus.PENDING, RunStatus.RETRYING)),
        or_(AnalysisRun.next_attempt_at.is_(None), AnalysisRun.next_attempt_at <= now),
        AnalysisRun.deadline_at.is_not(None),
        AnalysisRun.deadline_at > now,
    )
    statement = (
        select(AnalysisRun)
        .where(eligible)
        .order_by(AnalysisRun.created_at.asc(), AnalysisRun.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    run = (await session.execute(statement)).scalar_one_or_none()
    if run is None:
        return None
    if run.deadline_at is None:  # guarded in SQL; keeps the typed boundary fail-closed
        raise ValueError("eligible queued run is missing its deadline")
    expires_at = now + timedelta(seconds=lease_seconds)
    run.status = RunStatus.RUNNING
    run.lease_owner = lease_owner
    run.lease_expires_at = expires_at
    run.heartbeat_at = now
    run.attempt += 1
    run.fencing_token += 1
    run.next_attempt_at = None
    await session.flush()
    return LeaseClaim(
        run_id=run.id,
        agency_id=run.agency_id,
        transaction_id=run.transaction_id,
        lease_owner=lease_owner,
        fencing_token=run.fencing_token,
        attempt=run.attempt,
        workflow_mode=run.workflow_mode,
        model_override=run.model_override,
        lease_expires_at=expires_at,
        deadline_at=_utc(run.deadline_at),
    )


async def heartbeat_lease(
    session: AsyncSession,
    claim: LeaseClaim,
    *,
    now: datetime,
    lease_seconds: int,
) -> bool:
    """Extend only the live lease matching the claim's tenant, owner, and token."""
    statement = (
        update(AnalysisRun)
        .where(
            AnalysisRun.id == claim.run_id,
            AnalysisRun.agency_id == claim.agency_id,
            AnalysisRun.status == RunStatus.RUNNING,
            AnalysisRun.lease_owner == claim.lease_owner,
            AnalysisRun.fencing_token == claim.fencing_token,
            AnalysisRun.deadline_at > now,
        )
        .values(
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        )
    )
    result = await session.execute(statement.returning(AnalysisRun.id))
    return result.scalar_one_or_none() is not None


async def abandon_claim(session: AsyncSession, claim: LeaseClaim, *, now: datetime) -> bool:
    """Expire an exact live claim so the next reaper pass can retry it immediately."""
    statement = (
        update(AnalysisRun)
        .where(
            AnalysisRun.id == claim.run_id,
            AnalysisRun.agency_id == claim.agency_id,
            AnalysisRun.status == RunStatus.RUNNING,
            AnalysisRun.lease_owner == claim.lease_owner,
            AnalysisRun.fencing_token == claim.fencing_token,
        )
        .values(lease_expires_at=now)
    )
    result = await session.execute(statement.returning(AnalysisRun.id))
    return result.scalar_one_or_none() is not None


async def reap_expired_runs(
    session: AsyncSession,
    *,
    now: datetime,
    max_attempts: int,
    retry_backoff_seconds: int,
) -> ReapResult:
    """Move expired running leases to retrying or terminal failed state."""
    statement = (
        select(AnalysisRun)
        .where(
            AnalysisRun.status == RunStatus.RUNNING,
            AnalysisRun.lease_expires_at.is_not(None),
            AnalysisRun.lease_expires_at <= now,
        )
        .order_by(AnalysisRun.lease_expires_at.asc())
        .with_for_update(skip_locked=True)
    )
    rows = list((await session.execute(statement)).scalars().all())
    waiting_statement = (
        select(AnalysisRun)
        .where(
            AnalysisRun.status.in_((RunStatus.PENDING, RunStatus.RETRYING)),
            AnalysisRun.deadline_at.is_not(None),
            AnalysisRun.deadline_at <= now,
        )
        .order_by(AnalysisRun.deadline_at.asc())
        .with_for_update(skip_locked=True)
    )
    expired_waiting = (await session.execute(waiting_statement)).scalars().all()
    retried = 0
    failed = 0
    failed_runs: list[ReapedFailure] = []
    for run in rows:
        deadline_reached = run.deadline_at is None or _utc(run.deadline_at) <= _utc(now)
        if run.attempt >= max_attempts or deadline_reached:
            run.status = RunStatus.FAILED
            run.error_code = "run_attempts_exhausted" if not deadline_reached else "run_deadline"
            failed += 1
            failed_runs.append(
                ReapedFailure(
                    run_id=run.id,
                    agency_id=run.agency_id,
                    error_code=run.error_code,
                )
            )
        else:
            run.status = RunStatus.RETRYING
            run.next_attempt_at = now + timedelta(seconds=retry_backoff_seconds * run.attempt)
            retried += 1
        run.lease_owner = None
        run.lease_expires_at = None
        run.heartbeat_at = None
    for run in expired_waiting:
        run.status = RunStatus.FAILED
        run.error_code = "run_deadline"
        run.next_attempt_at = None
        failed += 1
        failed_runs.append(
            ReapedFailure(
                run_id=run.id,
                agency_id=run.agency_id,
                error_code=run.error_code,
            )
        )
    await session.flush()
    return ReapResult(
        retried=retried,
        failed=failed,
        failed_runs=tuple(failed_runs),
    )

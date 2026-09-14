"""Summary: Durable investigation worker loop with recovery, heartbeats, and fenced execution.

Key classes:
- DurableRunWorker: claim, heartbeat, execute, and recover queued investigation runs.

Key functions:
- (none)

Notes:
- API processes never share memory with this worker; persisted events are the observation channel.
- A cancelled or crashed execution retains/expedites its lease for bounded recovery by another pass.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.db.models import RunStatus
from fraudlens_backend.db.repositories import AnalysisRunRepository, TransactionRepository
from fraudlens_backend.middleware.logging import APP_LOGGER_NAME, get_logger
from fraudlens_backend.pipeline_policy import build_pipeline_input
from fraudlens_backend.pipeline_wiring import (
    PipelineComponents,
    build_pipeline_deps,
)
from fraudlens_backend.runs.leases import (
    LeaseClaim,
    LeaseLostError,
    abandon_claim,
    claim_next_run,
    heartbeat_lease,
)
from fraudlens_backend.runs.reaper import reap_stale_runs
from fraudlens_backend.settings import AppSettings
from fraudlens_ml.pipeline import Runner, StreamMessage

_MAX_WORKER_ID_LENGTH = 128


class DurableRunWorker:
    """Process-local scheduler that executes globally claimed runs under tenant-bound fencing."""

    def __init__(  # noqa: PLR0913 - explicit worker dependencies make tests deterministic.
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        components: PipelineComponents,
        settings: AppSettings,
        worker_id: str,
        clock: Callable[[], datetime] | None = None,
        heartbeat_file: Path | None = None,
    ) -> None:
        """Bind durable storage, shared pipeline components, worker identity, and time source."""
        self._sessionmaker = sessionmaker
        self._components = components
        self._settings = settings
        if not worker_id or len(worker_id) > _MAX_WORKER_ID_LENGTH:
            raise ValueError("worker id must contain between 1 and 128 characters")
        self._worker_id = worker_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._heartbeat_file = heartbeat_file or Path(settings.run_worker_heartbeat_file)

    async def run_once(self) -> bool:
        """Recover stale work, claim at most one run, and execute it; return whether one ran."""
        self._touch_liveness()
        async with self._sessionmaker() as session:
            await reap_stale_runs(session, self._settings, now=self._clock())
            claim = await claim_next_run(
                session,
                lease_owner=self._worker_id,
                now=self._clock(),
                lease_seconds=self._settings.run_lease_seconds,
            )
            await session.commit()
        if claim is None:
            return False
        try:
            await self._execute_with_heartbeat(claim)
        except asyncio.CancelledError:
            raise
        except Exception:
            get_logger(APP_LOGGER_NAME).error(
                "investigation.worker_error",
                run_id=str(claim.run_id),
                attempt=claim.attempt,
                exc_info=True,
            )
            await self._abandon(claim)
        return True

    async def run_forever(self, stop: asyncio.Event) -> None:
        """Run bounded claim batches until a shutdown signal requests graceful exit."""
        while not stop.is_set():
            processed = False
            for _index in range(self._settings.run_claim_batch):
                if stop.is_set() or not await self.run_once():
                    break
                processed = True
            if not processed:
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        stop.wait(), timeout=self._settings.run_worker_poll_seconds
                    )

    async def _execute_with_heartbeat(self, claim: LeaseClaim) -> None:
        """Cancel execution if its independent lease heartbeat loses ownership."""
        finished = asyncio.Event()

        async def drive() -> None:
            try:
                await self._execute(claim)
            finally:
                finished.set()

        async with asyncio.TaskGroup() as group:
            group.create_task(drive())
            group.create_task(self._heartbeat(claim, finished))

    async def _execute(self, claim: LeaseClaim) -> None:
        """Reconstruct a tenant-scoped pipeline and drive it with the claim's fence."""
        async with self._sessionmaker() as session:
            transaction_repo = TransactionRepository(session, claim.agency_id)
            transaction = await transaction_repo.get(claim.transaction_id)
            if transaction is None:
                raise RuntimeError("claimed investigation transaction is unavailable")
            pipeline_input = await build_pipeline_input(
                repo=transaction_repo,
                transaction=transaction,
                run_id=claim.run_id,
                agency_id=claim.agency_id,
                settings=self._settings,
            )

            async def emit(_message: StreamMessage) -> None:
                """No-op live fan-out; API replicas poll the durable event log."""

            deps = await build_pipeline_deps(
                components=self._components,
                session=session,
                settings=self._settings,
                agency_id=claim.agency_id,
                run_id=claim.run_id,
                transaction_id=claim.transaction_id,
                emit=emit,
                model_override=claim.model_override,
                sessionmaker=self._sessionmaker,
                workflow_mode=claim.workflow_mode,
                lease_owner=claim.lease_owner,
                fencing_token=claim.fencing_token,
            )
            await Runner(deps).run(pipeline_input)

    async def _heartbeat(self, claim: LeaseClaim, finished: asyncio.Event) -> None:
        """Extend the claim periodically; a lost fence cancels the sibling pipeline task."""
        while True:
            try:
                await asyncio.wait_for(
                    finished.wait(), timeout=self._settings.run_heartbeat_seconds
                )
                return
            except TimeoutError:
                pass
            self._touch_liveness()
            async with self._sessionmaker() as session:
                extended = await heartbeat_lease(
                    session,
                    claim,
                    now=self._clock(),
                    lease_seconds=self._settings.run_lease_seconds,
                )
                if extended:
                    await session.commit()
                    continue
                await session.rollback()
                run = await AnalysisRunRepository(session, claim.agency_id).get(claim.run_id)
            if run is not None and run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
                return
            raise LeaseLostError("worker heartbeat lost its run fence")

    async def _abandon(self, claim: LeaseClaim) -> None:
        """Best-effort lease expiry after a known worker-side orchestration failure."""
        async with self._sessionmaker() as session:
            await abandon_claim(session, claim, now=self._clock())
            await session.commit()

    def _touch_liveness(self) -> None:
        """Atomically-enough refresh the worker liveness timestamp without run data."""
        self._heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        self._heartbeat_file.write_text(self._clock().isoformat(), encoding="utf-8")

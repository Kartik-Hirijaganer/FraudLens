"""Summary: Supervise durable upload screening in an inline application runtime.

Key classes:
- (none)

Key functions:
- upload_worker_lifespan: start, recover and stop the existing fenced worker.

Notes:
- External-worker mode keeps ownership in its separate worker process.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.middleware.logging import APP_LOGGER_NAME, get_logger
from fraudlens_backend.runs.worker import DurableRunWorker
from fraudlens_backend.settings import AppSettings

if TYPE_CHECKING:
    from fraudlens_backend.pipeline_wiring import PipelineComponents


@asynccontextmanager
async def upload_worker_lifespan(
    sessionmaker: async_sessionmaker[AsyncSession] | None,
    components: PipelineComponents,
    settings: AppSettings,
) -> AsyncIterator[None]:
    """Own a recoverable upload-only scheduler while the inline API is alive."""
    if sessionmaker is None or settings.run_execution_mode != "inline":
        yield
        return
    stop = asyncio.Event()
    worker = DurableRunWorker(
        sessionmaker=sessionmaker,
        components=components,
        settings=settings,
        worker_id=f"inline-{uuid.uuid4()}",
        uploads_only=True,
    )

    async def supervise() -> None:
        while not stop.is_set():
            try:
                await worker.run_forever(stop)
            except Exception:
                get_logger(APP_LOGGER_NAME).error("screening.scheduler_unavailable")
                await asyncio.sleep(settings.run_worker_poll_seconds)

    task = asyncio.create_task(supervise())
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

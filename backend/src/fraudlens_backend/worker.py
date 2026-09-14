"""Summary: Command-line entry point for the standalone durable investigation worker process.

Key classes:
- (none)

Key functions:
- run_worker: configure dependencies and serve until SIGINT/SIGTERM.
- main: run the asynchronous worker entry point.

Notes:
- The worker requires DATABASE_URL from Infisical-injected runtime configuration; errors never
  include the connection string.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
from contextlib import suppress

from fraudlens_backend.db.session import (
    build_sessionmaker,
    create_engine_from_settings,
    dispose_engine,
)
from fraudlens_backend.middleware.logging import configure_logging
from fraudlens_backend.pipeline_wiring import build_pipeline_components
from fraudlens_backend.runs.worker import DurableRunWorker
from fraudlens_backend.settings import get_settings
from fraudlens_backend.telemetry import init_telemetry


async def run_worker() -> None:
    """Build the worker runtime and serve queued investigations until graceful shutdown."""
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.environment != "dev")
    init_telemetry(settings)
    engine = create_engine_from_settings(settings)
    if engine is None:
        raise RuntimeError("durable investigation worker requires a configured database")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    worker = DurableRunWorker(
        sessionmaker=build_sessionmaker(engine),
        components=build_pipeline_components(settings),
        settings=settings,
        worker_id=f"{socket.gethostname()}-{os.getpid()}",
    )
    try:
        await worker.run_forever(stop)
    finally:
        await dispose_engine(engine)


def main() -> None:
    """Run the standalone worker process."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()

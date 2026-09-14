"""Summary: Shared deterministic dependencies for durable worker integration tests.

Key classes:
- (none)

Key functions:
- queue_run: seed one tenant transaction and pending investigation.
- worker_settings: build short deterministic lease settings.
- fake_pipeline_deps: bind fake compute ports to the real fenced persistence adapter.
- build_worker: construct a durable worker with an injected clock and liveness path.

Notes:
- SQLite and PostgreSQL tests share these helpers so only the storage engine varies.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from pipeline_fakes import (
    FakeExplainerPort,
    FakeRetrieverPort,
    FakeRulesPort,
    FakeSarDrafter,
    FakeScorerPort,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.db.models import Agency, Transaction
from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    ModelRegistryRepository,
    SarDraftRepository,
)
from fraudlens_backend.pipeline_runs import PipelineRunStore
from fraudlens_backend.pipeline_wiring import PipelineComponents
from fraudlens_backend.runs.worker import DurableRunWorker
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskPolicy
from fraudlens_ml.pipeline import PipelineDeps


async def queue_run(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    agency_id: uuid.UUID,
    now: datetime,
) -> uuid.UUID:
    """Seed one tenant transaction and queued run."""
    async with sessionmaker() as session:
        session.add(Agency(id=agency_id, name="Worker Test", slug=f"worker-{agency_id.hex}"))
        transaction = Transaction(
            agency_id=agency_id,
            external_id=f"worker-{uuid.uuid4().hex}",
            amount=Decimal("9500.00"),
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
        run = await AnalysisRunRepository(session, agency_id).create_pending(
            transaction_id=transaction.id,
            deadline_at=now + timedelta(minutes=5),
            request_fingerprint="a" * 64,
        )
        await session.commit()
        return run.id


def worker_settings(make_settings: Callable[..., AppSettings]) -> AppSettings:
    """Build worker settings whose lease/backoff can be advanced by a fake clock."""
    return make_settings(
        run_execution_mode="worker",
        run_lease_seconds=2,
        run_heartbeat_seconds=1,
        run_retry_backoff_seconds=1,
    )


async def fake_pipeline_deps(**kwargs: Any) -> PipelineDeps:
    """Return deterministic ports around the real fenced persistence adapter."""
    session = cast(AsyncSession, kwargs["session"])
    agency_id = cast(uuid.UUID, kwargs["agency_id"])
    run_id = cast(uuid.UUID, kwargs["run_id"])
    transaction_id = cast(uuid.UUID, kwargs["transaction_id"])
    return PipelineDeps(
        rules=FakeRulesPort(),
        scorer=FakeScorerPort(),
        explainer=FakeExplainerPort(),
        retriever=FakeRetrieverPort(),
        drafter=FakeSarDrafter(),
        store=PipelineRunStore(
            session=session,
            run_id=run_id,
            transaction_id=transaction_id,
            analysis=AnalysisRunRepository(session, agency_id),
            registry=ModelRegistryRepository(session),
            sar=SarDraftRepository(session, agency_id),
            lease_owner=cast(str, kwargs["lease_owner"]),
            fencing_token=cast(int, kwargs["fencing_token"]),
        ),
        emit=kwargs["emit"],
        risk_policy=RiskPolicy(),
    )


def build_worker(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: AppSettings,
    worker_id: str,
    clock: Callable[[], datetime],
    heartbeat_file: Path,
) -> DurableRunWorker:
    """Build one durable worker around deterministic test dependencies."""
    return DurableRunWorker(
        sessionmaker=sessionmaker,
        components=cast(PipelineComponents, object()),
        settings=settings,
        worker_id=worker_id,
        clock=clock,
        heartbeat_file=heartbeat_file,
    )

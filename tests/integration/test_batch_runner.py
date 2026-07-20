"""Tests for the batch investigation job runner (plan §16 Phase 8): it drives the pipeline over a
set of transactions headlessly and records a `job_executions(batch_score)` row. The pipeline deps
are faked (build_pipeline_deps patched to a real `PipelineRunStore` + fake ports), so the test
asserts the batch orchestration + job recording + `select_uninvestigated` (no heavy model)."""

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
    FakeSarDrafter,
    FakeScorerPort,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import fraudlens_backend.jobs.runner as batch
from fraudlens_backend.db.models import (
    Agency,
    AnalysisRun,
    JobExecution,
    JobType,
    RunStatus,
    Transaction,
)
from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    ModelRegistryRepository,
    SarDraftRepository,
)
from fraudlens_backend.jobs.runner import BatchScoreResult, run_batch_score, select_uninvestigated
from fraudlens_backend.pipeline_wiring import PipelineRunStore, build_pipeline_components
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskPolicy
from fraudlens_ml.pipeline import PipelineDeps

_AGENCY_ID = uuid.UUID("77777777-7777-4777-8777-777777777777")


def _transaction(external_id: str, **overrides: object) -> Transaction:
    """A masked transaction row for the batch agency."""
    body: dict[str, object] = {
        "agency_id": _AGENCY_ID,
        "external_id": external_id,
        "amount": Decimal("9500.00"),
        "currency": "USD",
        "occurred_at": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        "origin_account": "****1111",
        "dest_account": "****2222",
        "channel": "wire",
        "country": "US",
        "features": {},
        "feature_hash": "fh",
    }
    body.update(overrides)
    return Transaction(**body)  # type: ignore[arg-type]


def _patch_fake_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the batch runner's build_pipeline_deps to use fakes + the real store."""

    async def fake_build_deps(
        *,
        session: AsyncSession,
        agency_id: uuid.UUID,
        run_id: uuid.UUID,
        transaction_id: uuid.UUID,
        emit: object,
        **_kwargs: object,
    ) -> PipelineDeps:
        store = PipelineRunStore(
            session=session,
            run_id=run_id,
            transaction_id=transaction_id,
            analysis=AnalysisRunRepository(session, agency_id),
            registry=ModelRegistryRepository(session),
            sar=SarDraftRepository(session, agency_id),
        )
        return PipelineDeps(
            rules=FakeRulesPort(),
            scorer=FakeScorerPort(),
            explainer=FakeExplainerPort(),
            retriever=FakeRetrieverPort(),
            drafter=FakeSarDrafter(),
            store=store,
            emit=emit,  # type: ignore[arg-type]
            risk_policy=RiskPolicy(),
        )

    monkeypatch.setattr(batch, "build_pipeline_deps", fake_build_deps)


async def test_select_uninvestigated_skips_already_scored(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        session.add(Agency(id=_AGENCY_ID, name="B", slug="b-batch"))
        scored = _transaction("scored", latest_run_id=uuid.uuid4())
        session.add_all([_transaction("a"), _transaction("b"), scored])
        await session.commit()
        ids = await select_uninvestigated(session, agency_id=_AGENCY_ID)
    assert len(ids) == 2  # the already-scored transaction is excluded


async def test_run_batch_score_investigates_and_records_job(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_fake_deps(monkeypatch)
    async with db_sessionmaker() as session:
        session.add(Agency(id=_AGENCY_ID, name="B", slug="b-batch"))
        first = _transaction("a")
        second = _transaction("b")
        session.add_all([first, second])
        await session.commit()
        ids = [first.id, second.id, uuid.uuid4()]  # the last id does not exist → skipped

        result = await run_batch_score(
            session=session,
            components=build_pipeline_components(make_settings(llm_mode="mock")),
            settings=make_settings(),
            agency_id=_AGENCY_ID,
            transaction_ids=ids,
        )

    assert isinstance(result, BatchScoreResult)
    assert result.requested == 3
    assert result.completed == 2
    assert result.failed == 0
    assert result.skipped == 1

    async with db_sessionmaker() as session:
        completed = (
            await session.execute(
                select(func.count())
                .select_from(AnalysisRun)
                .where(AnalysisRun.status == RunStatus.COMPLETED)
            )
        ).scalar_one()
        assert completed == 2
        job = (
            await session.execute(
                select(JobExecution).where(JobExecution.job_type == JobType.BATCH_SCORE)
            )
        ).scalar_one()
        assert job.result == {"completed": 2, "failed": 0, "skipped": 1}
        assert str(job.id) == result.job_id


async def test_run_batch_score_isolates_a_poisoned_transaction(
    make_settings: Callable[..., AppSettings],
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One row violating a tightened contract fails ITS run; the sweep still completes.

    A zero amount can only exist as legacy storage predating the cent-quantization boundary;
    `build_pipeline_input` raises on it, and the runner must isolate the fault instead of
    aborting the whole batch.
    """
    _patch_fake_deps(monkeypatch)
    async with db_sessionmaker() as session:
        session.add(Agency(id=_AGENCY_ID, name="B", slug="b-batch"))
        good = _transaction("good-row")
        poisoned = _transaction("poisoned-row", amount=Decimal("0.00"))
        session.add_all([good, poisoned])
        await session.commit()
        good_id, poisoned_id = good.id, poisoned.id  # plain values survive the rollback expiry

        result = await run_batch_score(
            session=session,
            components=build_pipeline_components(make_settings(llm_mode="mock")),
            settings=make_settings(),
            agency_id=_AGENCY_ID,
            transaction_ids=[good_id, poisoned_id],
        )

    assert result.completed == 1
    assert result.failed == 1
    async with db_sessionmaker() as session:
        failed_run = (
            await session.execute(
                select(AnalysisRun).where(AnalysisRun.transaction_id == poisoned_id)
            )
        ).scalar_one()
        assert failed_run.status is RunStatus.FAILED
        assert failed_run.error_code == "batch_input_error"

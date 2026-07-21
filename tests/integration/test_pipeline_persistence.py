"""Integration tests for the backend `PipelineRunStore` + `AnalysisRunRepository` (plan §16
Phase 8): driving the Runner with fake ports but the REAL store over an in-memory DB, asserting the
durable rows the investigation persists — the ordered `analysis_run_events` (SSE replay log), the
immutable `analysis_results`, the `rag_retrievals`, the SAR draft, the hash-only
`model_inference_logs`, the conditional open `alerts` row, and the completed run + the transaction's
stamped latest-run/band. Also covers the unregistered-label inference-log skip and run failure."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from pipeline_fakes import (
    FakeExplainerPort,
    FakeRetrieverPort,
    FakeRulesPort,
    FakeSarDrafter,
    FakeScorerPort,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fraudlens_backend.db.models import (
    Agency,
    Alert,
    AlertOrigin,
    AlertStatus,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunEvent,
    ModelInferenceLog,
    ModelTrainingRun,
    ModelTrigger,
    ModelVersion,
    RagRetrieval,
    RunStatus,
    SarDraft,
    Severity,
    TrainingDataset,
    Transaction,
)
from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    ModelRegistryRepository,
    SarDraftRepository,
)
from fraudlens_backend.pipeline_wiring import PipelineRunStore
from fraudlens_core import RiskBand, RiskPolicy, RuleContext
from fraudlens_core.rules.base import RuleTransaction
from fraudlens_ml.pipeline import PipelineDeps, PipelineInput, Runner

_AGENCY_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
_VERSION_ID = uuid.UUID("33333333-3333-4333-8333-0000000000aa")


async def _noop_emit(_message: object) -> None:
    """A no-op emitter (persistence tests do not assert the live broadcast)."""


async def _seed(session: AsyncSession, *, version_label: str = "v-test") -> Transaction:
    """Seed the agency, the model version (for inference-log resolution), and a transaction."""
    session.add(Agency(id=_AGENCY_ID, name="Persist Co", slug="persist-co"))
    dataset_id = uuid.uuid4()
    run_id = uuid.uuid4()
    session.add(
        TrainingDataset(id=dataset_id, label_window="synthetic", row_count=0, content_hash="h")
    )
    session.add(ModelTrainingRun(id=run_id, trigger=ModelTrigger.MANUAL, dataset_id=dataset_id))
    session.add(
        ModelVersion(
            id=_VERSION_ID,
            version_label=version_label,
            training_run_id=run_id,
            artifact_uri=version_label,
        )
    )
    transaction = Transaction(
        agency_id=_AGENCY_ID,
        external_id="T-persist",
        amount=Decimal("9500.00"),
        currency="USD",
        occurred_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        origin_account="****1111",
        dest_account="****2222",
        channel="wire",
        country="US",
        features={},
        feature_hash="fh-persist",
    )
    session.add(transaction)
    await session.commit()
    return transaction


def _pipeline_input(*, run_id: uuid.UUID, transaction: Transaction) -> PipelineInput:
    """Build the PipelineInput the Runner consumes (the fake ports ignore the context)."""
    txn = RuleTransaction(
        amount=transaction.amount,
        currency=transaction.currency,
        country=transaction.country,
        channel=transaction.channel,
        occurred_at=transaction.occurred_at,
    )
    return PipelineInput(
        agency_id=str(_AGENCY_ID),
        run_id=str(run_id),
        transaction_id=str(transaction.id),
        rule_context=RuleContext(transaction=txn),
        amount=transaction.amount,
        currency=transaction.currency,
        country=transaction.country,
        channel=transaction.channel,
        feature_hash=transaction.feature_hash,
    )


def _deps(
    session: AsyncSession, *, run_id: uuid.UUID, transaction_id: uuid.UUID, **overrides: object
) -> PipelineDeps:
    """Assemble PipelineDeps with fake ports and the REAL PipelineRunStore over the session."""
    store = PipelineRunStore(
        session=session,
        run_id=run_id,
        transaction_id=transaction_id,
        analysis=AnalysisRunRepository(session, _AGENCY_ID),
        registry=ModelRegistryRepository(session),
        sar=SarDraftRepository(session, _AGENCY_ID),
    )
    parts: dict[str, object] = {
        "rules": FakeRulesPort(),
        "scorer": FakeScorerPort(),
        "explainer": FakeExplainerPort(),
        "retriever": FakeRetrieverPort(),
        "drafter": FakeSarDrafter(),
        "store": store,
        "emit": _noop_emit,
        "risk_policy": RiskPolicy(),
    }
    parts.update(overrides)
    return PipelineDeps(**parts)  # type: ignore[arg-type]


async def test_run_persists_all_rows_and_completes(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        transaction = await _seed(session)
        run = await AnalysisRunRepository(session, _AGENCY_ID).create_running(
            transaction_id=transaction.id
        )
        await session.commit()
        report = await Runner(_deps(session, run_id=run.id, transaction_id=transaction.id)).run(
            _pipeline_input(run_id=run.id, transaction=transaction)
        )

    assert report.status == "completed"
    async with db_sessionmaker() as session:
        events = (
            (
                await session.execute(
                    select(AnalysisRunEvent)
                    .where(AnalysisRunEvent.run_id == run.id)
                    .order_by(AnalysisRunEvent.seq.asc())
                )
            )
            .scalars()
            .all()
        )
        assert [event.event_type.value for event in events] == [
            "run.started",
            "step.rules.completed",
            "step.scoring.completed",
            "step.shap.completed",
            "step.rag.completed",
            "sar.started",
            "run.completed",
        ]
        assert [event.seq for event in events] == [1, 2, 3, 4, 5, 6, 7]  # gap-free ordering

        result = (
            await session.execute(select(AnalysisResult).where(AnalysisResult.run_id == run.id))
        ).scalar_one()
        assert result.risk_band is RiskBand.HIGH
        assert result.model_version == "v-test"

        retrieval = (
            await session.execute(select(RagRetrieval).where(RagRetrieval.run_id == run.id))
        ).scalar_one()
        assert retrieval.rag_version == "rag-test"

        sar = (
            await session.execute(select(SarDraft).where(SarDraft.run_id == run.id))
        ).scalar_one()
        assert sar.status.value == "draft"

        inference = (
            await session.execute(
                select(ModelInferenceLog).where(ModelInferenceLog.run_id == run.id)
            )
        ).scalar_one()
        assert inference.was_canary is False
        assert inference.model_version_id == _VERSION_ID
        assert inference.feature_hash == "fh-persist"

        alert = (await session.execute(select(Alert).where(Alert.run_id == run.id))).scalar_one()
        assert alert.origin is AlertOrigin.PIPELINE
        assert alert.status is AlertStatus.OPEN
        assert alert.severity is Severity.HIGH

        completed_run = (
            await session.execute(select(AnalysisRun).where(AnalysisRun.id == run.id))
        ).scalar_one()
        assert completed_run.status is RunStatus.COMPLETED
        assert completed_run.risk_band is RiskBand.HIGH
        assert completed_run.model_version == "v-test"
        assert completed_run.prompt_version == "sar-v1"

        scored_txn = (
            await session.execute(select(Transaction).where(Transaction.id == transaction.id))
        ).scalar_one()
        assert scored_txn.latest_run_id == run.id
        assert scored_txn.risk_band is RiskBand.HIGH


async def test_low_score_completes_without_alert(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        transaction = await _seed(session)
        run = await AnalysisRunRepository(session, _AGENCY_ID).create_running(
            transaction_id=transaction.id
        )
        await session.commit()
        await Runner(
            _deps(
                session,
                run_id=run.id,
                transaction_id=transaction.id,
                scorer=FakeScorerPort(probability=0.1),
            )
        ).run(_pipeline_input(run_id=run.id, transaction=transaction))

    async with db_sessionmaker() as session:
        alert_count = (await session.execute(select(func.count()).select_from(Alert))).scalar_one()
        retrieval_count = (
            await session.execute(select(func.count()).select_from(RagRetrieval))
        ).scalar_one()
        sar_count = (await session.execute(select(func.count()).select_from(SarDraft))).scalar_one()
        assert alert_count == 0
        assert retrieval_count == 0
        assert sar_count == 0


async def test_unregistered_model_label_skips_inference_log(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        transaction = await _seed(session)
        run = await AnalysisRunRepository(session, _AGENCY_ID).create_running(
            transaction_id=transaction.id
        )
        await session.commit()
        await Runner(
            _deps(
                session,
                run_id=run.id,
                transaction_id=transaction.id,
                scorer=FakeScorerPort(label="unregistered"),
            )
        ).run(_pipeline_input(run_id=run.id, transaction=transaction))

    async with db_sessionmaker() as session:
        inference_count = (
            await session.execute(select(func.count()).select_from(ModelInferenceLog))
        ).scalar_one()
        assert inference_count == 0  # an unregistered label cannot be hash-logged (skipped)


async def test_scoring_failure_persists_failed_run_and_partial_log(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        transaction = await _seed(session)
        run = await AnalysisRunRepository(session, _AGENCY_ID).create_running(
            transaction_id=transaction.id
        )
        await session.commit()
        report = await Runner(
            _deps(
                session,
                run_id=run.id,
                transaction_id=transaction.id,
                scorer=FakeScorerPort(error=True),
            )
        ).run(_pipeline_input(run_id=run.id, transaction=transaction))

    assert report.status == "failed"
    async with db_sessionmaker() as session:
        failed_run = (
            await session.execute(select(AnalysisRun).where(AnalysisRun.id == run.id))
        ).scalar_one()
        assert failed_run.status is RunStatus.FAILED
        assert failed_run.error_code == "investigation_failed"
        events = (
            (
                await session.execute(
                    select(AnalysisRunEvent)
                    .where(AnalysisRunEvent.run_id == run.id)
                    .order_by(AnalysisRunEvent.seq.asc())
                )
            )
            .scalars()
            .all()
        )
        # partial log: started + rules persisted before the failure, then run.failed
        assert [event.event_type.value for event in events] == [
            "run.started",
            "step.rules.completed",
            "run.failed",
        ]
        result_count = (
            await session.execute(select(func.count()).select_from(AnalysisResult))
        ).scalar_one()
        assert result_count == 0

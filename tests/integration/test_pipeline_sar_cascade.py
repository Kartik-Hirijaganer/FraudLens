"""End-to-end investigation through the quality-gated SAR cascade (release 0.5.0 Phase 3).

The other cascade suites exercise the drafter in isolation. This one drives the REAL pipeline
graph — rules → scoring → SHAP → RAG → SAR → persistence → the SSE replay log — with a real
`QualityGatedSarDrafter` whose first tier is rejected, and asserts the things an analyst and an
auditor actually depend on:

- the escalation reaches the durable event channel as ordered `sar.stage.*` / `sar.escalated`
  rows, and a real reconnect through `_event_stream` replays those decisions as SSE frames
  rather than losing them;
- no rejected tier's narrative is in any persisted event payload or in the stored draft — a
  reconnecting client reproduces the DECISIONS without the content that was refused;
- the persisted draft carries a passing verdict and its escalation tier;
- an analyst cannot approve a draft the cascade failed, and cannot approve one persisted without
  a verdict at all — the repository refuses to create it in the first place.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pipeline_fakes import (
    FakeExplainerPort,
    FakeRetrieverPort,
    FakeRulesPort,
    FakeScorerPort,
    passing_quality,
)
from quality_gates import production_gate
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tenancy import new_agency_id

import fraudlens_backend.pipeline_wiring as wiring
from fraudlens_backend.api.v1.investigations import _event_stream
from fraudlens_backend.db.models import (
    Agency,
    AnalysisRun,
    AnalysisRunEvent,
    ModelTrainingRun,
    ModelTrigger,
    ModelVersion,
    RunStatus,
    SarDraft,
    SarQualityStatus,
    SarStatus,
    TrainingDataset,
    Transaction,
)
from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    ModelRegistryRepository,
    SarDraftRepository,
)
from fraudlens_backend.db.repositories.sar import SarQualityGateNotSatisfiedError
from fraudlens_backend.models.alerts import SarReviewDecision
from fraudlens_backend.pipeline_wiring import PipelineComponents, PipelineRunStore, RunManager
from fraudlens_backend.sar.budget import BudgetGuard
from fraudlens_backend.sar.drafter_gated import QualityGatedSarDrafter, SarCascadeTier
from fraudlens_backend.services.alert_workflow import sar_decision_allowed
from fraudlens_core import RiskPolicy, RuleContext
from fraudlens_core.rules.base import RuleTransaction
from fraudlens_ml.pipeline import PipelineDeps, PipelineInput, Runner
from fraudlens_ml.sar import (
    SarDraftContent,
    SarDraftResult,
    SarDraftStatus,
    SarEventType,
    SarGateReason,
    SarGenerationAttempt,
    SarInput,
    SarStreamEvent,
    SarTokenUsage,
)

_AGENCY_ID = new_agency_id()
_VERSION_ID = uuid.UUID("44444444-4444-4444-8444-0000000000aa")
_REJECTED_NARRATIVE = "rejected-awq-narrative-sentinel"
_ACCEPTED_NARRATIVE = "The transaction shows structuring indicators."
_REJECTED = production_gate().rejected(SarGateReason.CITATION_FABRICATED)


class _CascadeTierDrafter:
    """A tier that streams its own tokens then one scripted terminal result."""

    def __init__(self, stage: str, *, passes: bool) -> None:
        """Bind the stage name and whether its draft clears the gate."""
        self.stage = stage
        self.calls = 0
        self._passes = passes

    async def draft(self, _sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        """Emit one token then the scripted terminal result for this stage."""
        self.calls += 1
        narrative = _ACCEPTED_NARRATIVE if self._passes else _REJECTED_NARRATIVE
        yield SarStreamEvent(type=SarEventType.TOKEN, token=narrative)
        quality = passing_quality() if self._passes else _REJECTED
        yield SarStreamEvent(
            type=SarEventType.COMPLETED if self._passes else SarEventType.FAILED,
            result=SarDraftResult(
                status=SarDraftStatus.DRAFT if self._passes else SarDraftStatus.FAILED,
                content=narrative if self._passes else "",
                structured=SarDraftContent(
                    subject="Structuring",
                    narrative=narrative,
                    recommended_action="Escalate for human review.",
                )
                if self._passes
                else None,
                model_id=f"vllm/{self.stage}",
                prompt_version="sar-v2",
                prompt_hash="hash",
                error_code=None if self._passes else "sar_quality_gate_failed",
                token_usage=SarTokenUsage(output_tokens=8, total_tokens=8),
                cost_usd=Decimal("0"),
                quality=quality,
                attempts=(
                    SarGenerationAttempt(
                        ordinal=0,
                        stage=self.stage,
                        model_id=f"vllm/{self.stage}",
                        connection=f"runpod-{self.stage}",
                        outcome="passed" if self._passes else "rejected",
                        quality=quality,
                        token_usage=SarTokenUsage(output_tokens=8, total_tokens=8),
                        cost_usd=Decimal("0"),
                        prompt_hash="hash",
                    ),
                ),
            ),
        )


def _cascade(*tiers: _CascadeTierDrafter) -> QualityGatedSarDrafter:
    """Wrap scripted tiers in the production cascade with the committed policy."""
    return QualityGatedSarDrafter(
        tiers=tuple(SarCascadeTier(name=tier.stage, drafter=tier) for tier in tiers),
        gate=production_gate(),
        budget=BudgetGuard(),
    )


async def _noop_emit(_message: object) -> None:
    """A no-op emitter: this suite asserts the DURABLE log an SSE reconnect replays."""


async def _seed(session: AsyncSession) -> Transaction:
    """Seed the agency, model version, and the transaction the investigation runs on."""
    session.add(Agency(id=_AGENCY_ID, name="Cascade Co", slug="cascade-co"))
    dataset_id, training_run_id = uuid.uuid4(), uuid.uuid4()
    session.add(
        TrainingDataset(id=dataset_id, label_window="synthetic", row_count=0, content_hash="h")
    )
    session.add(
        ModelTrainingRun(id=training_run_id, trigger=ModelTrigger.MANUAL, dataset_id=dataset_id)
    )
    session.add(
        ModelVersion(
            id=_VERSION_ID,
            version_label="v-test",
            training_run_id=training_run_id,
            artifact_uri="v-test",
        )
    )
    transaction = Transaction(
        agency_id=_AGENCY_ID,
        external_id="T-cascade",
        amount=Decimal("9500.00"),
        currency="USD",
        occurred_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        origin_account="****1111",
        dest_account="****2222",
        channel="wire",
        country="US",
        features={},
        feature_hash="fh-cascade",
    )
    session.add(transaction)
    await session.commit()
    return transaction


def _pipeline_input(*, run_id: uuid.UUID, transaction: Transaction) -> PipelineInput:
    """Build the PipelineInput the Runner consumes for this seeded transaction."""
    return PipelineInput(
        agency_id=str(_AGENCY_ID),
        run_id=str(run_id),
        transaction_id=str(transaction.id),
        source="synthetic-generator",
        rule_context=RuleContext(
            transaction=RuleTransaction(
                amount=transaction.amount,
                currency=transaction.currency,
                country=transaction.country,
                channel=transaction.channel,
                occurred_at=transaction.occurred_at,
            )
        ),
        amount=transaction.amount,
        currency=transaction.currency,
        country=transaction.country,
        channel=transaction.channel,
        feature_hash=transaction.feature_hash,
    )


def _deps(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    transaction_id: uuid.UUID,
    drafter: QualityGatedSarDrafter,
) -> PipelineDeps:
    """Assemble the real store over the session with fake upstream ports and a real cascade."""
    return PipelineDeps(
        rules=FakeRulesPort(),
        scorer=FakeScorerPort(),
        explainer=FakeExplainerPort(),
        retriever=FakeRetrieverPort(),
        drafter=drafter,
        store=PipelineRunStore(
            session=session,
            run_id=run_id,
            transaction_id=transaction_id,
            analysis=AnalysisRunRepository(session, _AGENCY_ID),
            registry=ModelRegistryRepository(session),
            sar=SarDraftRepository(session, _AGENCY_ID),
        ),
        emit=_noop_emit,
        risk_policy=RiskPolicy(),
    )


async def _run_investigation(
    db_sessionmaker: async_sessionmaker[AsyncSession], drafter: QualityGatedSarDrafter
) -> tuple[uuid.UUID, object]:
    """Seed, run one full investigation through the cascade, and return the run id + report."""
    async with db_sessionmaker() as session:
        transaction = await _seed(session)
        run = await AnalysisRunRepository(session, _AGENCY_ID).create_running(
            transaction_id=transaction.id
        )
        await session.commit()
        report = await Runner(
            _deps(session, run_id=run.id, transaction_id=transaction.id, drafter=drafter)
        ).run(_pipeline_input(run_id=run.id, transaction=transaction))
    return run.id, report


async def _events(
    db_sessionmaker: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> list[AnalysisRunEvent]:
    """Return the durable, ordered event log an SSE reconnect replays from Last-Event-ID."""
    async with db_sessionmaker() as session:
        rows = await session.execute(
            select(AnalysisRunEvent)
            .where(AnalysisRunEvent.run_id == run_id)
            .order_by(AnalysisRunEvent.seq.asc())
        )
        return list(rows.scalars().all())


@pytest.mark.asyncio
async def test_an_escalated_investigation_persists_its_stage_decisions_in_order(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The escalation is durable: a reconnecting client replays every stage decision."""
    awq, bf16 = _CascadeTierDrafter("awq", passes=False), _CascadeTierDrafter("bf16", passes=True)

    run_id, report = await _run_investigation(db_sessionmaker, _cascade(awq, bf16))
    events = await _events(db_sessionmaker, run_id)
    types = [event.event_type.value for event in events]

    assert report.status == "completed"
    assert (awq.calls, bf16.calls) == (1, 1)
    assert types == [
        "run.started",
        "step.rules.completed",
        "step.scoring.completed",
        "step.shap.completed",
        "step.rag.completed",
        "sar.started",
        "sar.stage.started",
        "sar.stage.rejected",
        "sar.escalated",
        "sar.stage.started",
        "run.completed",
    ]
    assert [event.seq for event in events] == list(range(1, len(events) + 1))


@pytest.mark.asyncio
async def test_the_replayed_stage_decisions_carry_reason_codes_but_no_rejected_content(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Reconnect reproduces WHY a tier was refused, never WHAT it wrote."""
    run_id, _ = await _run_investigation(
        db_sessionmaker,
        _cascade(
            _CascadeTierDrafter("awq", passes=False), _CascadeTierDrafter("bf16", passes=True)
        ),
    )
    events = await _events(db_sessionmaker, run_id)
    by_type = {event.event_type.value: event.payload for event in events}

    rejected = by_type["sar.stage.rejected"]
    assert rejected["stage"] == "awq"
    assert rejected["reasons"] == [SarGateReason.CITATION_FABRICATED.value]
    assert by_type["sar.escalated"]["stage"] == "bf16"
    assert by_type["sar.escalated"]["ordinal"] == 1
    assert all(_REJECTED_NARRATIVE not in str(event.payload) for event in events)


@pytest.mark.asyncio
async def test_the_persisted_draft_carries_its_verdict_and_escalation_tier(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The stored SAR records that it was gated, and which tier actually served it."""
    run_id, _ = await _run_investigation(
        db_sessionmaker,
        _cascade(
            _CascadeTierDrafter("awq", passes=False), _CascadeTierDrafter("bf16", passes=True)
        ),
    )

    async with db_sessionmaker() as session:
        draft = (
            await session.execute(select(SarDraft).where(SarDraft.run_id == run_id))
        ).scalar_one()
        attempts = await SarDraftRepository(session, _AGENCY_ID).list_attempts(draft.id)

    assert draft.status is SarStatus.DRAFT
    assert draft.quality_status is SarQualityStatus.PASSED
    assert draft.quality["passed"] is True
    assert _REJECTED_NARRATIVE not in draft.content
    assert [attempt.stage for attempt in attempts] == ["awq", "bf16"]
    assert [attempt.outcome for attempt in attempts] == ["rejected", "passed"]
    assert all(attempt.agency_id == _AGENCY_ID for attempt in attempts)
    # The serving tier is not a column on the draft; it is reconstructable EXACTLY from the
    # tenant-scoped attempt rows, which is the provenance guarantee Phase 2.9 designed for.
    served = next(attempt for attempt in attempts if attempt.outcome == "passed")
    assert served.ordinal == 1


@pytest.mark.asyncio
async def test_a_fully_failed_cascade_completes_the_run_without_a_reviewable_draft(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Every tier rejected means no SAR to approve — and the failure is on the record."""
    tiers = (_CascadeTierDrafter("awq", passes=False), _CascadeTierDrafter("bf16", passes=False))

    run_id, _ = await _run_investigation(db_sessionmaker, _cascade(*tiers))
    types = [event.event_type.value for event in await _events(db_sessionmaker, run_id)]

    async with db_sessionmaker() as session:
        draft = (
            await session.execute(select(SarDraft).where(SarDraft.run_id == run_id))
        ).scalar_one()
        run = (
            await session.execute(select(AnalysisRun).where(AnalysisRun.id == run_id))
        ).scalar_one()

    assert "sar.cascade.failed" in types
    assert draft.status is SarStatus.FAILED
    assert draft.quality_status is SarQualityStatus.FAILED
    assert draft.quality["passed"] is False
    assert draft.content == ""
    assert run.status is RunStatus.COMPLETED
    # An analyst cannot approve what the cascade refused: approval of a failed draft is only
    # legal when the reviewer supplies replacement content of their own.
    assert not sar_decision_allowed(draft.status, SarReviewDecision.APPROVE, has_edit=False)
    assert sar_decision_allowed(draft.status, SarReviewDecision.REJECT, has_edit=False)


@pytest.mark.asyncio
async def test_an_ungated_result_can_never_be_persisted_for_approval(
    db_session: AsyncSession,
) -> None:
    """Approval is impossible for an ungated draft because the row cannot exist at all."""
    repo = SarDraftRepository(db_session, _AGENCY_ID)
    ungated = SarDraftResult(
        status=SarDraftStatus.DRAFT,
        content="Structuring indicators were identified.",
        model_id="vllm/awq",
        prompt_version="sar-v2",
        prompt_hash="hash",
    )

    with pytest.raises(SarQualityGateNotSatisfiedError):
        await repo.create_from_result(run_id=uuid.uuid4(), result=ungated)


def _event_name(frame: str) -> str:
    """Extract the `event:` line value from one SSE frame."""
    return next(line[len("event: ") :] for line in frame.splitlines() if line.startswith("event: "))


def _components(make_settings) -> PipelineComponents:
    """Build the real components the stream manager is constructed over."""
    return wiring.build_pipeline_components(make_settings(llm_mode="mock"))


@pytest.mark.asyncio
async def test_a_reconnecting_client_replays_the_stage_decisions_without_the_rejected_draft(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    make_settings,
) -> None:
    """The reconnect path itself: replayed SSE frames carry the decisions, not the refused text."""
    run_id, _ = await _run_investigation(
        db_sessionmaker,
        _cascade(
            _CascadeTierDrafter("awq", passes=False), _CascadeTierDrafter("bf16", passes=True)
        ),
    )
    manager = RunManager(
        sessionmaker=db_sessionmaker,
        components=_components(make_settings),
        settings=make_settings(),
    )

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
        "step.scoring.completed",
        "step.shap.completed",
        "step.rag.completed",
        "sar.started",
        "sar.stage.started",
        "sar.stage.rejected",
        "sar.escalated",
        "sar.stage.started",
        "run.completed",
    ]
    replayed = "".join(frames)
    assert SarGateReason.CITATION_FABRICATED.value in replayed
    assert _REJECTED_NARRATIVE not in replayed
    # A client that already saw the rejection resumes after it and replays only what followed.
    resumed = [
        _event_name(frame)
        async for frame in _event_stream(
            manager=manager,
            sessionmaker=db_sessionmaker,
            agency_id=_AGENCY_ID,
            run_id=run_id,
            after_seq=8,
        )
    ]
    assert resumed == ["sar.escalated", "sar.stage.started", "run.completed"]

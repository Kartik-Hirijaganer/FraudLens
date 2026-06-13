"""Unit tests for the LangGraph investigation pipeline (plan §16 Phase 8): event ordering,
persistence calls, the risk-blend band→alert decision, deterministic-core failure → run.failed +
partial log, soft RAG/LLM degradation, and live token streaming. Driven entirely through injected
fakes + an in-memory `FakeRunStore` (no DB, no heavy ML), so they assert the orchestration contract
the real adapters/store implement (plan "pure nodes + injected IO")."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pipeline_fakes import (
    FakeExplainerPort,
    FakeRetrieverPort,
    FakeRulesPort,
    FakeRunStore,
    FakeSarDrafter,
    FakeScorerPort,
    RecordingEmit,
)

from fraudlens_core import RiskBand, RiskPolicy, RuleContext
from fraudlens_core.rules.base import RuleTransaction
from fraudlens_ml.pipeline import PipelineDeps, PipelineInput, Runner
from fraudlens_ml.sar import SarDraftStatus

_FULL_SEQUENCE = [
    "run.started",
    "step.rules.completed",
    "step.scoring.completed",
    "step.shap.completed",
    "step.rag.completed",
    "sar.started",
    "run.completed",
]


def _pipeline_input(**overrides: object) -> PipelineInput:
    """Build a PHI-free PipelineInput for the fakes (a high-amount wire from the US)."""
    txn = RuleTransaction(
        amount=Decimal("9500"),
        currency="USD",
        country="US",
        channel="wire",
        occurred_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    params: dict[str, object] = {
        "agency_id": "a1",
        "run_id": "r1",
        "transaction_id": "t1",
        "rule_context": RuleContext(transaction=txn),
        "amount": Decimal("9500"),
        "currency": "USD",
        "country": "US",
        "channel": "wire",
        "feature_hash": "fh",
    }
    params.update(overrides)
    return PipelineInput(**params)  # type: ignore[arg-type]


def _deps(store: FakeRunStore, emit: RecordingEmit, **overrides: object) -> PipelineDeps:
    """Assemble PipelineDeps from the fakes, overriding any single collaborator."""
    parts: dict[str, object] = {
        "rules": FakeRulesPort(),
        "scorer": FakeScorerPort(),
        "explainer": FakeExplainerPort(),
        "retriever": FakeRetrieverPort(),
        "drafter": FakeSarDrafter(),
        "store": store,
        "emit": emit,
        "risk_policy": RiskPolicy(),
    }
    parts.update(overrides)
    return PipelineDeps(**parts)  # type: ignore[arg-type]


async def test_happy_path_persists_full_event_sequence_and_records() -> None:
    store, emit = FakeRunStore(), RecordingEmit()
    report = await Runner(_deps(store, emit)).run(_pipeline_input())

    assert store.event_types == _FULL_SEQUENCE
    assert report.status == "completed"
    assert report.risk_band is RiskBand.HIGH  # 0.9*0.7 + 0.5*0.3 = 0.78 → high band
    assert report.sar_status == SarDraftStatus.DRAFT.value
    assert len(store.results) == 1
    assert len(store.rags) == 1
    assert len(store.sars) == 1
    assert len(store.inferences) == 1
    assert len(store.completed) == 1
    assert store.failed == []


async def test_high_score_raises_alert_low_score_does_not() -> None:
    high_store = FakeRunStore()
    await Runner(_deps(high_store, RecordingEmit())).run(_pipeline_input())
    assert len(high_store.alerts) == 1  # combined 0.78 >= 0.6 alert threshold

    low_store = FakeRunStore()
    report = await Runner(
        _deps(low_store, RecordingEmit(), scorer=FakeScorerPort(probability=0.1))
    ).run(_pipeline_input())
    assert report.risk_band is RiskBand.LOW  # 0.1*0.7 + 0.5*0.3 = 0.22 → low band
    assert low_store.alerts == []


async def test_tokens_stream_live_but_are_not_persisted() -> None:
    store, emit = FakeRunStore(), RecordingEmit()
    await Runner(_deps(store, emit, drafter=FakeSarDrafter(tokens=("a", "b", "c")))).run(
        _pipeline_input()
    )

    assert emit.event_types.count("sar.token") == 3  # streamed live
    assert "sar.token" not in store.event_types  # never persisted to the replay log
    started = emit.event_types.index("sar.started")
    completed = emit.event_types.index("run.completed")
    assert all(
        started < emit.event_types.index("sar.token") < completed
        for _ in range(1)  # tokens fall between sar.started and the terminal run.completed
    )
    assert emit.event_types[0] == "run.started"
    assert emit.event_types[-1] == "run.completed"


async def test_scoring_event_payload_carries_probability_and_model_version() -> None:
    store = FakeRunStore()
    await Runner(_deps(store, RecordingEmit())).run(_pipeline_input())
    scoring = dict(store.events)["step.scoring.completed"]
    assert scoring["fraudProbability"] == 0.9
    assert scoring["modelVersion"] == "v-test"
    completed = dict(store.events)["run.completed"]
    assert completed["riskBand"] == RiskBand.HIGH.value
    assert completed["sarDraftId"].startswith("sar-")


async def test_scoring_failure_marks_run_failed_with_partial_log() -> None:
    store, emit = FakeRunStore(), RecordingEmit()
    report = await Runner(_deps(store, emit, scorer=FakeScorerPort(error=True))).run(
        _pipeline_input()
    )

    assert store.event_types == ["run.started", "step.rules.completed", "run.failed"]
    assert report.status == "failed"
    assert report.error_code == "investigation_failed"
    assert len(store.failed) == 1
    assert store.results == []  # the deterministic core never reached the result snapshot
    assert store.completed == []


async def test_rules_failure_marks_run_failed_immediately() -> None:
    store = FakeRunStore()
    report = await Runner(_deps(store, RecordingEmit(), rules=FakeRulesPort(error=True))).run(
        _pipeline_input()
    )
    assert store.event_types == ["run.started", "run.failed"]
    assert report.status == "failed"


async def test_rag_failure_degrades_but_run_completes() -> None:
    store = FakeRunStore()
    report = await Runner(
        _deps(store, RecordingEmit(), retriever=FakeRetrieverPort(error=True))
    ).run(_pipeline_input())

    assert store.event_types == _FULL_SEQUENCE  # the run still completes end-to-end
    assert report.status == "completed"
    assert store.rags[0].rag_version == "unknown"  # degraded to an empty retrieval
    assert store.rags[0].chunks == []


async def test_sar_failure_persists_failed_draft_but_run_completes() -> None:
    store = FakeRunStore()
    report = await Runner(
        _deps(store, RecordingEmit(), drafter=FakeSarDrafter(status=SarDraftStatus.FAILED))
    ).run(_pipeline_input())

    assert report.status == "completed"  # the LLM is a soft enhancer (plan §10.6)
    assert report.sar_status == SarDraftStatus.FAILED.value
    assert store.sars[0].status is SarDraftStatus.FAILED
    assert store.event_types == _FULL_SEQUENCE


async def test_drafter_without_terminal_event_degrades_to_failed_sentinel() -> None:
    store = FakeRunStore()
    report = await Runner(
        _deps(store, RecordingEmit(), drafter=FakeSarDrafter(no_terminal=True))
    ).run(_pipeline_input())

    assert report.status == "completed"
    assert store.sars[0].status is SarDraftStatus.FAILED
    assert store.sars[0].error_code == "sar_drafter_error"


async def test_drafter_raising_mid_stream_degrades_to_failed_sentinel() -> None:
    store = FakeRunStore()
    report = await Runner(
        _deps(store, RecordingEmit(), drafter=FakeSarDrafter(raise_mid=True))
    ).run(_pipeline_input())

    assert report.status == "completed"  # a drafter fault never fails the run (plan §7.5)
    assert store.sars[0].status is SarDraftStatus.FAILED


async def test_drafter_empty_token_is_skipped_not_streamed() -> None:
    store, emit = FakeRunStore(), RecordingEmit()
    await Runner(
        _deps(store, emit, drafter=FakeSarDrafter(tokens=("real",), include_empty_token=True))
    ).run(_pipeline_input())

    assert emit.event_types.count("sar.token") == 1  # the value-less token is not broadcast

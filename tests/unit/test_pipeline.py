"""Unit tests for the LangGraph investigation pipeline (plan §16 Phase 8): event ordering,
persistence calls, the risk-blend band→alert decision, deterministic-core failure → run.failed +
partial log, soft RAG/LLM degradation, and live token streaming. Driven entirely through injected
fakes + an in-memory `FakeRunStore` (no DB, no heavy ML), so they assert the orchestration contract
the real adapters/store implement (plan "pure nodes + injected IO")."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

import pytest
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
from fraudlens_ml.pipeline.runner import log_core_failure
from fraudlens_ml.sar import (
    SarAgentEvent,
    SarDraftContent,
    SarDraftResult,
    SarDraftStatus,
    SarEventType,
    SarStreamEvent,
)

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
        "source": "synthetic-generator",
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
    assert high_store.operations.index("raise_alert") < high_store.operations.index("save_rag")

    low_store = FakeRunStore()
    report = await Runner(
        _deps(low_store, RecordingEmit(), scorer=FakeScorerPort(probability=0.1))
    ).run(_pipeline_input())
    assert report.risk_band is RiskBand.LOW  # 0.1*0.7 + 0.5*0.3 = 0.22 → low band
    assert low_store.alerts == []
    assert low_store.rags == []
    assert low_store.sars == []
    assert low_store.event_types == [
        "run.started",
        "step.rules.completed",
        "step.scoring.completed",
        "step.shap.completed",
        "run.completed",
    ]
    assert report.sar_draft_id is None
    assert report.sar_status is None


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


async def test_agent_lifecycle_persists_while_tool_event_remains_ephemeral() -> None:
    class _AgentEventDrafter:
        """Emit one agent lifecycle with an ephemeral tool event and terminal draft."""

        async def draft(self, sar_input):
            agent = SarAgentEvent(
                agent_run_id="agent-run-1",
                agent="evidence_investigator",
                attempt=1,
            )
            yield SarStreamEvent(type=SarEventType.AGENT_STARTED, agent=agent)
            yield SarStreamEvent(
                type=SarEventType.AGENT_TOOL_COMPLETED,
                agent=agent.model_copy(update={"tool_name": "rule_hits", "status": "completed"}),
            )
            yield SarStreamEvent(
                type=SarEventType.AGENT_COMPLETED,
                agent=agent.model_copy(update={"status": "completed"}),
            )
            result = SarDraftResult(
                status=SarDraftStatus.DRAFT,
                content="Synthetic draft.",
                structured=SarDraftContent(
                    subject="Synthetic review",
                    narrative="Synthetic draft.",
                    recommended_action="Escalate for human review.",
                ),
                model_id="mock",
                prompt_version="v1",
                prompt_hash="hash",
            )
            yield SarStreamEvent(type=SarEventType.COMPLETED, result=result)

    store, emit = FakeRunStore(), RecordingEmit()
    await Runner(_deps(store, emit, drafter=_AgentEventDrafter())).run(_pipeline_input())

    assert "agent.started" in store.event_types
    assert "agent.completed" in store.event_types
    assert "agent.tool.completed" not in store.event_types
    assert "agent.tool.completed" in emit.event_types
    started_payload = dict(store.events)["agent.started"]
    assert started_payload["agentRunId"] == "agent-run-1"


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


async def test_drafter_raising_after_terminal_preserves_captured_result() -> None:
    store = FakeRunStore()
    report = await Runner(
        _deps(store, RecordingEmit(), drafter=FakeSarDrafter(raise_after_terminal=True))
    ).run(_pipeline_input())

    assert report.status == "completed"
    assert store.sars[0].status is SarDraftStatus.DRAFT
    assert store.sars[0].error_code is None


async def test_drafter_empty_token_is_skipped_not_streamed() -> None:
    store, emit = FakeRunStore(), RecordingEmit()
    await Runner(
        _deps(store, emit, drafter=FakeSarDrafter(tokens=("real",), include_empty_token=True))
    ).run(_pipeline_input())

    assert emit.event_types.count("sar.token") == 1  # the value-less token is not broadcast


async def test_core_failure_logs_the_exception_type_but_never_its_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The handler must name WHICH error was raised and WHERE, without leaking `str(exc)`.

    The stable `investigation_failed` code is deliberately opaque, so the log is the only place a
    production core failure is identifiable. It carries type/module/frames — source identity —
    and must never carry the message, which is where an exception embeds its inputs.
    """
    store, emit = FakeRunStore(), RecordingEmit()
    with caplog.at_level(logging.ERROR, logger="fraudlens.pipeline.runner"):
        report = await Runner(_deps(store, emit, scorer=FakeScorerPort(error=True))).run(
            _pipeline_input()
        )

    assert report.error_code == "investigation_failed"
    rendered = caplog.text
    assert "error_type=RuntimeError" in rendered
    assert "error_module=builtins" in rendered
    assert "pipeline_fakes.py:" in rendered  # the frame chain names the raise site
    assert "scorer boom" not in rendered  # the PHI invariant: no exception message, ever

    # `origin` must name OUR code, not whichever library detected the problem. The first version
    # reported the deepest frame overall and its first real failure named `asyncpg.py:797` — true,
    # and three libraries below anything actionable.
    origin = rendered.split("origin=", 1)[1].split(" ", 1)[0]
    assert origin.startswith("fraudlens"), origin


def test_core_failure_log_names_the_constraint_but_not_the_colliding_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A wrapped DBAPI error must surface WHICH constraint lost, never the keys that collided.

    `session.flush()` writes every pending object, so the raising frame names where the flush
    happened rather than which row lost. The constraint name closes that gap and is schema
    identity — it appears in the migration. Postgres's DETAIL line, which carries the offending
    key values, must not follow it into the log.
    """

    class _FakeUniqueViolationError(Exception):
        constraint_name = "uq_sar_generation_attempts_draft_id_ordinal"

        def __str__(self) -> str:
            return "duplicate key value violates ... DETAIL: Key (draft_id, ordinal)=(d1, 0)."

    class _FakeIntegrityError(Exception):
        def __init__(self) -> None:
            super().__init__("wrapped driver failure")
            self.orig = _FakeUniqueViolationError()

    with caplog.at_level(logging.ERROR, logger="fraudlens.pipeline.runner"):
        try:
            raise _FakeIntegrityError
        except _FakeIntegrityError as exc:
            log_core_failure(exc, run_id="run-1")

    rendered = caplog.text
    assert "cause_type=_FakeUniqueViolationError" in rendered
    assert "constraint=uq_sar_generation_attempts_draft_id_ordinal" in rendered
    assert "DETAIL" not in rendered  # the key values never follow the constraint name
    assert "(d1, 0)" not in rendered


def test_core_failure_log_tolerates_an_exception_with_no_dbapi_cause(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Most core failures are not database errors; the constraint fields degrade, never raise."""
    with caplog.at_level(logging.ERROR, logger="fraudlens.pipeline.runner"):
        try:
            raise ValueError("plain failure")
        except ValueError as exc:
            log_core_failure(exc, run_id="run-2")

    assert "cause_type=none" in caplog.text
    assert "sqlstate=none" in caplog.text
    assert "constraint=none" in caplog.text
    assert "plain failure" not in caplog.text


def test_core_failure_log_reaches_a_constraint_two_links_down(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The constraint lives on the DRIVER error, not on SQLAlchemy's adapted wrapper.

    The asyncpg dialect translates the driver error into its own DBAPI class and re-raises it
    `from` the original, copying the SQLSTATE across but not the constraint. Reading `exc.orig`
    alone returned `constraint=none` against a real unique violation in production; only the
    exception at `__cause__` knows which constraint lost.
    """

    class _DriverUniqueViolationError(Exception):
        sqlstate = "23505"
        constraint_name = "uq_sar_generation_attempts_draft_id_ordinal"

    class _AdaptedIntegrityError(Exception):
        """Stands in for the dialect's wrapper: carries SQLSTATE, knows no constraint."""

        sqlstate = "23505"
        pgcode = "23505"

    class _OrmIntegrityError(Exception):
        def __init__(self, orig: Exception) -> None:
            super().__init__("wrapped")
            self.orig = orig

    adapted = _AdaptedIntegrityError()
    adapted.__cause__ = _DriverUniqueViolationError()

    with caplog.at_level(logging.ERROR, logger="fraudlens.pipeline.runner"):
        try:
            raise _OrmIntegrityError(adapted)
        except _OrmIntegrityError as exc:
            log_core_failure(exc, run_id="run-3")

    rendered = caplog.text
    assert "sqlstate=23505" in rendered
    assert "constraint=uq_sar_generation_attempts_draft_id_ordinal" in rendered


def test_core_failure_log_names_the_column_for_a_not_null_violation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A NOT NULL violation has no named constraint, so the column is what locates it.

    Production returned sqlstate=23502 with constraint=none. That was the driver being correct,
    not the lookup failing: Postgres reports 23502 against a column, and only a unique or foreign
    key violation carries a constraint name. Collect all three locators rather than choosing one
    from the SQLSTATE.
    """

    class _DriverNotNullViolationError(Exception):
        sqlstate = "23502"
        constraint_name = None
        table_name = "sar_generation_attempts"
        column_name = "policy_hash"

    class _AdaptedIntegrityError(Exception):
        sqlstate = "23502"

    class _OrmIntegrityError(Exception):
        def __init__(self, orig: Exception) -> None:
            super().__init__("wrapped")
            self.orig = orig

    adapted = _AdaptedIntegrityError()
    adapted.__cause__ = _DriverNotNullViolationError()

    with caplog.at_level(logging.ERROR, logger="fraudlens.pipeline.runner"):
        try:
            raise _OrmIntegrityError(adapted)
        except _OrmIntegrityError as exc:
            log_core_failure(exc, run_id="run-4")

    rendered = caplog.text
    assert "sqlstate=23502" in rendered
    assert "constraint=none" in rendered  # correct for 23502, not a lookup failure
    assert "table=sar_generation_attempts" in rendered
    assert "column=policy_hash" in rendered

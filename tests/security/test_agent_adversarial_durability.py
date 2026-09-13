"""Adversarial agent SSE replay, idempotency, and restart durability tests."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from agent_fakes import (
    _AUTHORITY_REF,
    _CITATION,
    _config,
    _prompts,
    _RoleRuntime,
)
from agent_security_fakes import (
    _draft,
    _graph_outcomes,
    _sar_input,
    _seed_transaction,
    _sse_event_names,
    _wire_app,
)
from portfolio_demo_identity import DEMO_AGENCY_ID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fraudlens_backend.agents.config import AgentRole
from fraudlens_backend.agents.contracts import (
    AgentExecutionRecord,
    AgentExecutionStatus,
    ReviewDecision,
    ReviewVerdict,
)
from fraudlens_backend.agents.graph import build_agent_graph
from fraudlens_backend.agents.resume import AgentExecutionReplay
from fraudlens_backend.agents.runtime import agent_input_hash
from fraudlens_backend.db.models import (
    Agency,
    AgentExecution,
    AnalysisRun,
    AnalysisRunEvent,
    RunStatus,
    Transaction,
)
from fraudlens_backend.db.models.enums import AnalysisRunEventType
from fraudlens_backend.db.repositories import AgentExecutionRepository
from fraudlens_backend.main import create_app
from fraudlens_backend.settings import AppSettings
from fraudlens_ml.sar import (
    SarStreamEvent,
)


async def test_sse_reconnect_replays_only_events_after_last_event_id(
    make_security_app: Callable[..., Any],
    aclient: Callable[[Any], httpx.AsyncClient],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    agency_id = DEMO_AGENCY_ID
    transaction_id = uuid.uuid4()
    run_id = uuid.uuid4()
    async with db_sessionmaker() as session:
        session.add(Agency(id=agency_id, name="Demo Agency", slug="demo"))
        session.add(
            Transaction(
                id=transaction_id,
                agency_id=agency_id,
                external_id="security-sse",
                amount=Decimal("100.00"),
                currency="USD",
                occurred_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
                origin_account="masked-a",
                dest_account="masked-b",
                channel="wire",
                country="US",
                features={},
                feature_hash="security-sse-hash",
            )
        )
        session.add(
            AnalysisRun(
                id=run_id,
                agency_id=agency_id,
                transaction_id=transaction_id,
                status=RunStatus.COMPLETED,
            )
        )
        for seq, event_type in enumerate(
            (
                AnalysisRunEventType.RUN_STARTED,
                AnalysisRunEventType.STEP_RULES_COMPLETED,
                AnalysisRunEventType.AGENT_STARTED,
                AnalysisRunEventType.AGENT_COMPLETED,
                AnalysisRunEventType.RUN_COMPLETED,
            ),
            start=1,
        ):
            session.add(
                AnalysisRunEvent(
                    agency_id=agency_id,
                    run_id=run_id,
                    seq=seq,
                    event_type=event_type,
                    payload={},
                )
            )
        await session.commit()

    app = make_security_app(environment="dev", auth_dev_bypass=True)
    _wire_app(app, db_engine, db_sessionmaker)
    async with aclient(app) as client:
        replay = await client.get(
            f"/api/v1/investigations/{run_id}/stream",
            headers={"Last-Event-ID": "2"},
        )

    assert replay.status_code == 200
    assert _sse_event_names(replay.text) == [
        "agent.started",
        "agent.completed",
        "run.completed",
    ]
    assert "event: run.started" not in replay.text


async def test_duplicate_submission_is_idempotent_across_process_restart(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    agency_id = DEMO_AGENCY_ID
    async with db_sessionmaker() as session:
        session.add(Agency(id=agency_id, name="Demo Agency", slug="demo"))
        transaction = await _seed_transaction(
            session,
            agency_id=agency_id,
            label="idempotent",
            occurred_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        )
        await session.commit()
        transaction_id = transaction.id

    def restarted_app() -> Any:
        app = create_app(make_settings(environment="dev", auth_dev_bypass=True))
        _wire_app(app, db_engine, db_sessionmaker)
        app.state.run_manager.start = lambda **_kwargs: None
        return app

    headers = {"Idempotency-Key": "k1"}
    body = {"transactionId": str(transaction_id)}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted_app()),
        base_url="http://test",
    ) as client:
        first = await client.post("/api/v1/investigations", json=body, headers=headers)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted_app()),
        base_url="http://test",
    ) as client:
        duplicate = await client.post("/api/v1/investigations", json=body, headers=headers)

    assert first.status_code == 202 and duplicate.status_code == 202
    assert first.json()["runId"] == duplicate.json()["runId"]
    async with db_sessionmaker() as session:
        run_count = (
            await session.execute(select(func.count()).select_from(AnalysisRun))
        ).scalar_one()
        persisted_key = (await session.execute(select(AnalysisRun.idempotency_key))).scalar_one()
    assert run_count == 1
    assert persisted_key != headers["Idempotency-Key"] and len(persisted_key) == 64


async def test_process_restart_replays_completed_attempt_without_provider_reexecution(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    agency_id = uuid.uuid4()
    run_id = uuid.uuid4()
    transaction_id = uuid.uuid4()
    sar_input = _sar_input(transaction_id)
    prompts = _prompts()
    base_user_content = json.dumps(
        sar_input.model_dump(mode="json", by_alias=True, exclude={"agency_id"}),
        sort_keys=True,
        separators=(",", ":"),
    )
    evidence_result, tool_calls = _graph_outcomes()[AgentRole.EVIDENCE_INVESTIGATOR][0]
    completed_evidence = AgentExecutionRecord(
        agent=AgentRole.EVIDENCE_INVESTIGATOR,
        attempt=1,
        status=AgentExecutionStatus.COMPLETED,
        model_id=_config().agents.evidence_investigator.model,
        prompt_version=prompts[AgentRole.EVIDENCE_INVESTIGATOR].prompt_version,
        prompt_hash=prompts[AgentRole.EVIDENCE_INVESTIGATOR].prompt_hash,
        input_hash=agent_input_hash(
            agent=AgentRole.EVIDENCE_INVESTIGATOR,
            prompt=prompts[AgentRole.EVIDENCE_INVESTIGATOR],
            user_content=base_user_content,
        ),
        result_hash="security-replay-result",
        latency_ms=1,
        result=evidence_result.model_dump(mode="json", by_alias=True),
        tool_calls=tool_calls,
    )
    async with db_sessionmaker() as session:
        session.add(Agency(id=agency_id, name="Replay", slug=f"replay-{agency_id.hex}"))
        session.add(
            Transaction(
                id=transaction_id,
                agency_id=agency_id,
                external_id="security-replay",
                amount=Decimal("9500.00"),
                currency="USD",
                occurred_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
                origin_account="masked-a",
                dest_account="masked-b",
                channel="wire",
                country="US",
                features={},
                feature_hash="security-replay-hash",
            )
        )
        session.add(
            AnalysisRun(
                id=run_id,
                agency_id=agency_id,
                transaction_id=transaction_id,
                status=RunStatus.RUNNING,
            )
        )
        await session.flush()
        await AgentExecutionRepository(session, agency_id).create_from_record(
            run_id=run_id,
            record=completed_evidence,
        )
        await session.commit()

    outcomes = _graph_outcomes()
    outcomes[AgentRole.EVIDENCE_INVESTIGATOR] = ()
    outcomes[AgentRole.SAR_WRITER] = (
        (_draft(evidence_ref=_AUTHORITY_REF, citation_id=_CITATION), ()),
    )
    outcomes[AgentRole.COMPLIANCE_REVIEWER] = ((ReviewVerdict(decision=ReviewDecision.PASS), ()),)
    runtime = _RoleRuntime(outcomes)
    graph = build_agent_graph(
        runtime=runtime,
        config=_config(),
        prompts=prompts,
        run_id=run_id,
        replay=AgentExecutionReplay(
            db_sessionmaker,
            agency_id=agency_id,
            run_id=run_id,
        ),
    )

    result = await graph.run(sar_input, emit=_ignore_event)

    assert result.content is not None
    assert (AgentRole.EVIDENCE_INVESTIGATOR, 1) not in runtime.calls
    assert sum(record.cost_usd for record in result.executions) == Decimal("0")
    async with db_sessionmaker() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(AgentExecution)
                .where(
                    AgentExecution.agency_id == agency_id,
                    AgentExecution.run_id == run_id,
                    AgentExecution.agent == AgentRole.EVIDENCE_INVESTIGATOR,
                )
            )
        ).scalar_one()
    assert count == 1


async def _ignore_event(_event: SarStreamEvent) -> None:
    """Discard one lifecycle event in replay-focused tests."""

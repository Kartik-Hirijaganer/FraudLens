"""Investigation snapshot, replay, stream, and background-completion API tests."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from investigation_fakes import (
    _OTHER_AGENCY_ID,
    _client,
    _demo_app,
    _seed_completed_run,
    _seed_demo_transaction,
    _sse_events,
)
from pipeline_fakes import (
    FakeExplainerPort,
    FakeRetrieverPort,
    FakeRulesPort,
    FakeScorerPort,
)
from portfolio_demo_identity import DEMO_AGENCY_ID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

import fraudlens_backend.pipeline_wiring as wiring
from fraudlens_backend.agents.mock import MockAgentTeam
from fraudlens_backend.db.models import (
    Agency,
    AgentExecution,
    Alert,
    AnalysisRun,
    AnalysisRunEvent,
    RunStatus,
    SarDraft,
    SarStatus,
    SystemConfig,
)
from fraudlens_backend.db.models.enums import AnalysisRunEventType
from fraudlens_backend.db.repositories import (
    AgentExecutionRepository,
    AnalysisRunRepository,
    ModelRegistryRepository,
    SarDraftRepository,
)
from fraudlens_backend.pipeline_wiring import PipelineRunStore
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskPolicy
from fraudlens_ml.pipeline import PipelineDeps


async def test_snapshot_projects_run_result_and_sar(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _seed_completed_run(db_sessionmaker, agency_id=DEMO_AGENCY_ID, with_alert=True)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/investigations/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["attempt"] == 0
    assert body["maxAttempts"] == 3
    assert body["riskBand"] == "high"
    assert body["fraudProbability"] == 0.9
    assert body["sarStatus"] == "draft"
    async with db_sessionmaker() as session:
        alert_id = (
            await session.execute(
                select(Alert.id).where(
                    Alert.agency_id == DEMO_AGENCY_ID,
                    Alert.run_id == run_id,
                )
            )
        ).scalar_one()
    assert body["alertId"] == str(alert_id)
    assert body["citations"][0]["citation"] == "31 CFR 1010.314"
    assert body["retrievedRegulations"] == [
        {
            "chunkId": "fincen-structuring::0",
            "docId": "fincen-structuring",
            "citation": "31 CFR 1010.314",
            "title": "Structuring",
            "source": "FinCEN",
            "text": "No person shall structure a transaction.",
            "score": 0.98,
        }
    ]
    assert body["topFeatures"][0]["feature"] == "amount_log"


async def test_snapshot_alert_id_is_null_when_run_did_not_raise_alert(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _seed_completed_run(db_sessionmaker, agency_id=DEMO_AGENCY_ID)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/investigations/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["alertId"] is None


async def test_snapshot_cross_tenant_and_missing_return_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    other_run = await _seed_completed_run(db_sessionmaker, agency_id=_OTHER_AGENCY_ID)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        cross = await client.get(f"/api/v1/investigations/{other_run}")  # another agency's run
        missing = await client.get(f"/api/v1/investigations/{uuid.uuid4()}")
    assert cross.status_code == 404
    assert missing.status_code == 404
    assert cross.json()["code"] == "investigation_not_found"


async def test_stream_replays_terminal_run_and_blocks_cross_tenant(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Build a terminal run with a persisted event log under the demo agency.
    async with db_sessionmaker() as session:
        session.add(Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo-stream"))
        run = AnalysisRun(
            agency_id=DEMO_AGENCY_ID, transaction_id=uuid.uuid4(), status=RunStatus.COMPLETED
        )
        session.add(run)
        await session.flush()
        for seq, event_type in enumerate(
            [
                AnalysisRunEventType.RUN_STARTED,
                AnalysisRunEventType.STEP_RULES_COMPLETED,
                AnalysisRunEventType.RUN_COMPLETED,
            ],
            start=1,
        ):
            session.add(
                AnalysisRunEvent(
                    agency_id=DEMO_AGENCY_ID,
                    run_id=run.id,
                    seq=seq,
                    event_type=event_type,
                    payload={},
                )
            )
        await session.commit()
        run_id = run.id

    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/investigations/{run_id}/stream")
        body = resp.text
        cross = await client.get(f"/api/v1/investigations/{uuid.uuid4()}/stream")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert _sse_events(body) == ["run.started", "step.rules.completed", "run.completed"]
    assert cross.status_code == 404


async def test_run_completes_without_a_stream_then_snapshot_and_replay(
    make_settings: Callable[..., AppSettings],
    file_db: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessionmaker = file_db

    async def fake_build_deps(
        *,
        session: AsyncSession,
        agency_id: uuid.UUID,
        run_id: uuid.UUID,
        transaction_id: uuid.UUID,
        emit: Any,
        **_kwargs: Any,
    ) -> PipelineDeps:
        store = PipelineRunStore(
            session=session,
            run_id=run_id,
            transaction_id=transaction_id,
            analysis=AnalysisRunRepository(session, agency_id),
            registry=ModelRegistryRepository(session),
            sar=SarDraftRepository(session, agency_id),
        )

        async def record_execution(record: Any) -> None:
            async with sessionmaker() as execution_session:
                await AgentExecutionRepository(execution_session, agency_id).create_from_record(
                    run_id=run_id, record=record
                )
                await execution_session.commit()

        components = app.state.pipeline_components
        return PipelineDeps(
            rules=FakeRulesPort(),
            scorer=FakeScorerPort(),
            explainer=FakeExplainerPort(),
            retriever=FakeRetrieverPort(),
            drafter=MockAgentTeam(
                run_id=run_id,
                config=components.agent_config,
                prompts=components.agent_prompts,
                single_writer=components.drafter,
                record_execution=record_execution,
                request_revision=True,
            ),
            store=store,
            emit=emit,
            risk_policy=RiskPolicy(),
        )

    monkeypatch.setattr(wiring, "build_pipeline_deps", fake_build_deps)
    transaction_id = await _seed_demo_transaction(sessionmaker, external_id="full")
    async with sessionmaker() as session:
        session.add_all(
            [
                SystemConfig(
                    agency_id=DEMO_AGENCY_ID,
                    key="featureFlags",
                    value={"multiAgentSar": True},
                ),
                SystemConfig(agency_id=None, key="llmDailyBudgetUsd", value=5),
            ]
        )
        await session.commit()
    app = _demo_app(
        make_settings,
        engine,
        sessionmaker,
        multi_agent_sar_enabled=True,
    )

    async with _client(app) as client:
        start = await client.post(
            "/api/v1/investigations", json={"transactionId": str(transaction_id)}
        )
        run_id = start.json()["runId"]
        # No stream is ever connected — the run must complete on its own (ADR-016).
        await app.state.run_manager.join(run_id)
        snapshot = await client.get(f"/api/v1/investigations/{run_id}")
        stream = await client.get(f"/api/v1/investigations/{run_id}/stream")
        replay = await client.get(
            f"/api/v1/investigations/{run_id}/stream",
            headers={"Last-Event-ID": "6"},
        )

    assert start.status_code == 202
    assert snapshot.json()["status"] == "completed"
    assert snapshot.json()["riskBand"] == "high"
    assert snapshot.json()["sarStatus"] == "draft"
    assert snapshot.json()["sarContent"]
    assert snapshot.json()["retrievedRegulations"] == [
        {
            "chunkId": "d::0",
            "docId": "d",
            "citation": "31 CFR 1010.314",
            "title": "Structuring",
            "source": "FinCEN",
            "text": "No person shall structure a transaction.",
            "score": 0.98,
        }
    ]
    assert snapshot.json()["workflowMode"] == "multi_agent"
    assert snapshot.json()["graphVersion"] == "agents-v1"
    assert snapshot.json()["revisionCount"] == 1
    assert len(snapshot.json()["agentExecutions"]) == 6
    assert all(execution["modelCallCount"] >= 1 for execution in snapshot.json()["agentExecutions"])
    assert snapshot.json()["alertId"] is not None
    assert f'"alertId":"{snapshot.json()["alertId"]}"' in stream.text
    assert _sse_events(stream.text)[-1] == "run.completed"  # the full log replays post-hoc
    assert "agent.started" in _sse_events(stream.text)
    assert "agent.revision.requested" in _sse_events(stream.text)
    assert "run.started" not in _sse_events(replay.text)
    async with sessionmaker() as session:
        draft_status, draft_workflow = (
            await session.execute(
                select(SarDraft.status, SarDraft.workflow).where(
                    SarDraft.run_id == uuid.UUID(run_id)
                )
            )
        ).one()
        alert_status = (
            await session.execute(select(Alert.status).where(Alert.run_id == uuid.UUID(run_id)))
        ).scalar_one()
        execution_count = (
            await session.execute(
                select(func.count())
                .select_from(AgentExecution)
                .where(AgentExecution.run_id == uuid.UUID(run_id))
            )
        ).scalar_one()
    assert draft_status is SarStatus.DRAFT
    assert draft_workflow == "multi_agent"
    assert alert_status.value not in {"resolved", "dismissed"}
    assert execution_count == 6

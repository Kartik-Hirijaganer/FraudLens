"""Investigation API tests (plan §5.4, §16 Phase 8; endpoints 6-8): POST owns the run (202 + runId,
Idempotency-Key dedupe), 503 without a DB, 404 for a missing/cross-tenant run, the authoritative
snapshot projection, SSE replay of a terminal run, and the full POST→background-completion→snapshot
path proving a run completes with NO stream connected (ADR-016). The background path uses a
file-backed SQLite engine (own connections per session) + fake pipeline deps so it exercises real
persistence without the heavy model or shared-connection races."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from pipeline_fakes import (
    FakeExplainerPort,
    FakeRetrieverPort,
    FakeRulesPort,
    FakeSarDrafter,
    FakeScorerPort,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import fraudlens_backend.pipeline_wiring as wiring
from fraudlens_backend.db.models import (
    Agency,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunEvent,
    Base,
    JobStatus,
    ModelTrainingRun,
    ModelTrigger,
    ModelVersion,
    ModelVersionStatus,
    RunStatus,
    SarDraft,
    SarStatus,
    TrainingDataset,
    Transaction,
)
from fraudlens_backend.db.models.enums import AnalysisRunEventType
from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    ModelRegistryRepository,
    SarDraftRepository,
)
from fraudlens_backend.demo import DEMO_AGENCY_ID
from fraudlens_backend.main import create_app
from fraudlens_backend.pipeline_wiring import PipelineRunStore, RunManager
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskBand, RiskPolicy
from fraudlens_ml.pipeline import PipelineDeps

_OTHER_AGENCY_ID = uuid.UUID("66666666-6666-4666-8666-666666666666")


def _client(app: object) -> httpx.AsyncClient:
    """An AsyncClient driving the ASGI app in-process (same loop as the DB)."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _wire(app: object, engine: AsyncEngine, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """Point the app at a test engine/sessionmaker + a real RunManager over it."""
    app.state.db_engine = engine  # type: ignore[attr-defined]
    app.state.db_sessionmaker = sessionmaker  # type: ignore[attr-defined]
    app.state.run_manager = RunManager(  # type: ignore[attr-defined]
        sessionmaker=sessionmaker,
        components=app.state.pipeline_components,  # type: ignore[attr-defined]
        settings=app.state.settings,  # type: ignore[attr-defined]
    )


async def _seed_demo_transaction(
    sessionmaker: async_sessionmaker[AsyncSession], *, external_id: str = "T1"
) -> uuid.UUID:
    """Insert the demo agency + a transaction; return the transaction id."""
    async with sessionmaker() as session:
        if await session.get(Agency, DEMO_AGENCY_ID) is None:
            session.add(Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo-inv"))
        transaction = Transaction(
            agency_id=DEMO_AGENCY_ID,
            external_id=external_id,
            amount=Decimal("9500.00"),
            currency="USD",
            occurred_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            origin_account="****1111",
            dest_account="****2222",
            channel="wire",
            country="US",
            features={},
            feature_hash="fh",
        )
        session.add(transaction)
        await session.commit()
        return transaction.id


def _demo_app(
    make_settings: Callable[..., AppSettings],
    engine: AsyncEngine,
    sm: Any,
    **settings_overrides: Any,
) -> Any:
    """Build a dev-bypass app (tenant resolves to the demo agency) wired to the test DB."""
    app = create_app(make_settings(environment="dev", auth_dev_bypass=True, **settings_overrides))
    _wire(app, engine, sm)
    return app


async def _register_version(sm: async_sessionmaker[AsyncSession], *, label: str) -> None:
    """Register a model version (+ dataset/run) so a `modelOverride` to its label validates."""
    async with sm() as session:
        dataset = TrainingDataset(
            snapshot_query={}, label_window="t", row_count=0, feature_spec={}, content_hash="o" * 64
        )
        session.add(dataset)
        await session.flush()
        run = ModelTrainingRun(
            trigger=ModelTrigger.MANUAL, dataset_id=dataset.id, status=JobStatus.SUCCEEDED
        )
        session.add(run)
        await session.flush()
        session.add(
            ModelVersion(
                version_label=label,
                training_run_id=run.id,
                artifact_uri=label,
                feature_spec={},
                metrics={},
                status=ModelVersionStatus.CANDIDATE,
            )
        )
        await session.commit()


async def test_post_unknown_model_override_returns_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    transaction_id = await _seed_demo_transaction(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/investigations",
            json={"transactionId": str(transaction_id), "modelOverride": "no-such-version"},
        )
    assert resp.status_code == 404  # unregistered override rejected before the run starts (§5.4)
    assert resp.json()["code"] == "model_version_not_found"


async def test_post_with_registered_model_override_owns_run(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    transaction_id = await _seed_demo_transaction(db_sessionmaker)
    await _register_version(db_sessionmaker, label="override-cand")
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/investigations",
            json={"transactionId": str(transaction_id), "modelOverride": "override-cand"},
        )
    assert resp.status_code == 202  # a registered override passes the guard and owns a run
    assert resp.json()["runId"]


async def test_post_without_database_returns_503(
    make_settings: Callable[..., AppSettings],
) -> None:
    app = create_app(make_settings(environment="dev", auth_dev_bypass=True))  # no DB wired
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/investigations", json={"transactionId": str(uuid.uuid4())}
        )
    assert resp.status_code == 503  # the DB dependency fails closed before the handler runs
    assert resp.json()["code"] == "service_unavailable"


async def test_stream_without_database_returns_unavailable(
    make_settings: Callable[..., AppSettings],
) -> None:
    app = create_app(make_settings(environment="dev", auth_dev_bypass=True))  # no DB / no manager
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/investigations/{uuid.uuid4()}/stream")
    assert resp.status_code == 503


async def test_auditor_cannot_start_investigation(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    transaction_id = await _seed_demo_transaction(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker, auth_dev_bypass_role="auditor")
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/investigations", json={"transactionId": str(transaction_id)}
        )
    assert resp.status_code == 403
    assert resp.json()["code"] == "role_permission_required"


async def test_post_missing_transaction_returns_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_demo_transaction(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/investigations", json={"transactionId": str(uuid.uuid4())}
        )
    assert resp.status_code == 404
    assert resp.json()["code"] == "transaction_not_found"


async def test_post_owns_run_and_dedupes_idempotency_key(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    transaction_id = await _seed_demo_transaction(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    app.state.run_manager.start = lambda **_kwargs: None  # stub the background launch
    body = {"transactionId": str(transaction_id)}
    async with _client(app) as client:
        first = await client.post(
            "/api/v1/investigations", json=body, headers={"Idempotency-Key": "k1"}
        )
        second = await client.post(
            "/api/v1/investigations", json=body, headers={"Idempotency-Key": "k1"}
        )
        third = await client.post("/api/v1/investigations", json=body)  # no key → new run

    assert first.status_code == 202
    assert first.json()["runId"] == second.json()["runId"]  # double-click dedupe
    assert third.json()["runId"] != first.json()["runId"]
    async with db_sessionmaker() as session:
        run_count = (
            await session.execute(select(func.count()).select_from(AnalysisRun))
        ).scalar_one()
    assert run_count == 2  # the deduped POST created no extra run


async def _seed_completed_run(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    agency_id: uuid.UUID,
    with_result: bool = True,
) -> uuid.UUID:
    """Insert a completed run (+ result + SAR) under an agency; return the run id."""
    async with sessionmaker() as session:
        if await session.get(Agency, agency_id) is None:
            session.add(Agency(id=agency_id, name="A", slug=f"a-{agency_id.hex[:6]}"))
        transaction = Transaction(
            agency_id=agency_id,
            external_id=f"snap-{agency_id.hex[:6]}",
            amount=Decimal("9500.00"),
            currency="USD",
            occurred_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            origin_account="****1111",
            dest_account="****2222",
            channel="wire",
            country="US",
            features={},
            feature_hash="fh",
        )
        session.add(transaction)
        await session.flush()
        run = AnalysisRun(
            agency_id=agency_id,
            transaction_id=transaction.id,
            status=RunStatus.COMPLETED,
            risk_score=0.78,
            risk_band=RiskBand.HIGH,
            model_version="v-test",
        )
        session.add(run)
        await session.flush()
        if with_result:
            session.add(
                AnalysisResult(
                    agency_id=agency_id,
                    run_id=run.id,
                    fraud_probability=0.9,
                    shap_values={"amount_log": 0.4},
                    top_features=[{"feature": "amount_log", "value": 9.2, "shapValue": 0.4}],
                    rule_hits=[{"code": "structuring", "ruleType": "structuring"}],
                    combined_score=0.78,
                    risk_band=RiskBand.HIGH,
                    model_version="v-test",
                )
            )
            session.add(
                SarDraft(
                    agency_id=agency_id,
                    run_id=run.id,
                    model_id="mock",
                    prompt_version="sar-v1",
                    prompt_hash="h",
                    content="SAR",
                    structured={},
                    citations=[{"citation": "31 CFR 1010.314"}],
                    status=SarStatus.DRAFT,
                )
            )
        await session.commit()
        return run.id


async def test_snapshot_projects_run_result_and_sar(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _seed_completed_run(db_sessionmaker, agency_id=DEMO_AGENCY_ID)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/investigations/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["riskBand"] == "high"
    assert body["fraudProbability"] == 0.9
    assert body["sarStatus"] == "draft"
    assert body["citations"][0]["citation"] == "31 CFR 1010.314"
    assert body["topFeatures"][0]["feature"] == "amount_log"


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


def _sse_events(body: str) -> list[str]:
    """Extract the ordered `event:` names from an SSE response body."""
    names: list[str] = []
    for frame in body.split("\n\n"):
        for line in frame.splitlines():
            if line.startswith("event: "):
                names.append(line[len("event: ") :])
    return names


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


@pytest.fixture
async def file_db(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    """A file-backed SQLite engine so background + request sessions use separate connections."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'inv.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine, async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


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
        return PipelineDeps(
            rules=FakeRulesPort(),
            scorer=FakeScorerPort(),
            explainer=FakeExplainerPort(),
            retriever=FakeRetrieverPort(),
            drafter=FakeSarDrafter(),
            store=store,
            emit=emit,
            risk_policy=RiskPolicy(),
        )

    monkeypatch.setattr(wiring, "build_pipeline_deps", fake_build_deps)
    transaction_id = await _seed_demo_transaction(sessionmaker, external_id="full")
    app = _demo_app(make_settings, engine, sessionmaker)

    async with _client(app) as client:
        start = await client.post(
            "/api/v1/investigations", json={"transactionId": str(transaction_id)}
        )
        run_id = start.json()["runId"]
        # No stream is ever connected — the run must complete on its own (ADR-016).
        await app.state.run_manager.join(run_id)
        snapshot = await client.get(f"/api/v1/investigations/{run_id}")
        stream = await client.get(f"/api/v1/investigations/{run_id}/stream")

    assert start.status_code == 202
    assert snapshot.json()["status"] == "completed"
    assert snapshot.json()["riskBand"] == "high"
    assert snapshot.json()["sarStatus"] == "draft"
    assert _sse_events(stream.text)[-1] == "run.completed"  # the full log replays post-hoc


async def _seed_run_for_regen(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    agency_id: uuid.UUID,
    sar_status: SarStatus = SarStatus.DRAFT,
) -> uuid.UUID:
    """Insert a completed run with full evidence + a v1 SAR draft; return the run id."""
    async with sessionmaker() as session:
        if await session.get(Agency, agency_id) is None:
            session.add(Agency(id=agency_id, name="A", slug=f"regen-{agency_id.hex[:6]}"))
        transaction = Transaction(
            agency_id=agency_id,
            external_id=f"regen-{agency_id.hex[:6]}",
            amount=Decimal("48200.00"),
            currency="USD",
            occurred_at=datetime(2026, 6, 22, 12, 0, tzinfo=UTC),
            origin_account="****1111",
            dest_account="****2222",
            channel="wire",
            country="US",
            features={},
            feature_hash="fh",
        )
        session.add(transaction)
        await session.flush()
        run = AnalysisRun(
            agency_id=agency_id,
            transaction_id=transaction.id,
            status=RunStatus.COMPLETED,
            risk_score=0.87,
            risk_band=RiskBand.HIGH,
            model_version="v-test",
            rules_version="rules-v1",
            rag_version="rag-v1",
            prompt_version="sar-v1",
        )
        session.add(run)
        await session.flush()
        session.add(
            AnalysisResult(
                agency_id=agency_id,
                run_id=run.id,
                fraud_probability=0.87,
                shap_values={"amount_zscore": 0.42},
                top_features=[{"feature": "amount_zscore", "value": 4.1, "shapValue": 0.42}],
                rule_hits=[
                    {
                        "code": "STRUCTURING",
                        "ruleType": "structuring",
                        "severity": "high",
                        "weight": "0.5",
                        "reason": "near threshold",
                        "details": {},
                    }
                ],
                combined_score=0.87,
                risk_band=RiskBand.HIGH,
                model_version="v-test",
            )
        )
        session.add(
            SarDraft(
                agency_id=agency_id,
                run_id=run.id,
                version=1,
                model_id="mock",
                prompt_version="sar-v1",
                prompt_hash="h",
                content="Original SAR",
                structured={},
                citations=[
                    {
                        "citation": "31 CFR 1010.314",
                        "title": "CTR aggregation",
                        "source": "FinCEN",
                        "snippet": "…",
                    }
                ],
                status=sar_status,
            )
        )
        await session.commit()
        return run.id


async def test_regenerate_creates_next_sar_version(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _seed_run_for_regen(db_sessionmaker, agency_id=DEMO_AGENCY_ID)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(f"/api/v1/investigations/{run_id}/sar/regenerate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["runId"] == str(run_id)
    assert body["version"] == 2  # persisted as the next version, never overwriting v1
    assert body["status"] == "draft"
    assert body["content"]  # a freshly composed narrative
    # The grounded citation is reconstructed from the prior draft (grounding preserved).
    assert body["citations"][0]["citation"] == "31 CFR 1010.314"
    async with db_sessionmaker() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(SarDraft).where(SarDraft.run_id == run_id)
            )
        ).scalar_one()
    assert count == 2  # both the original and the regenerated draft persist


async def test_regenerate_tolerates_partial_stored_evidence(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # `_seed_completed_run` stores a rule hit + citation missing required fields; regeneration must
    # skip the un-revalidatable rows and still produce a draft rather than failing the request.
    run_id = await _seed_completed_run(db_sessionmaker, agency_id=DEMO_AGENCY_ID)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(f"/api/v1/investigations/{run_id}/sar/regenerate")
    assert resp.status_code == 200
    assert resp.json()["version"] == 2


async def test_regenerate_rejects_a_decided_draft(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _seed_run_for_regen(
        db_sessionmaker, agency_id=DEMO_AGENCY_ID, sar_status=SarStatus.APPROVED
    )
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(f"/api/v1/investigations/{run_id}/sar/regenerate")
    assert resp.status_code == 409
    assert resp.json()["code"] == "invalid_sar_transition"


async def test_regenerate_without_a_result_returns_409(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _seed_completed_run(db_sessionmaker, agency_id=DEMO_AGENCY_ID, with_result=False)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(f"/api/v1/investigations/{run_id}/sar/regenerate")
    assert resp.status_code == 409
    assert resp.json()["code"] == "sar_not_regenerable"


async def test_regenerate_cross_tenant_returns_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    other_run = await _seed_run_for_regen(db_sessionmaker, agency_id=_OTHER_AGENCY_ID)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(f"/api/v1/investigations/{other_run}/sar/regenerate")
    assert resp.status_code == 404
    assert resp.json()["code"] == "investigation_not_found"

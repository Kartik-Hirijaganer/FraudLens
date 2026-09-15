"""Investigation API tests (plan §5.4, §16 Phase 8; endpoints 6-8): POST owns the run (202 + runId,
Idempotency-Key dedupe), 503 without a DB, 404 for a missing/cross-tenant run, the authoritative
snapshot projection, SSE replay of a terminal run, and the full POST→background-completion→snapshot
path proving a run completes with NO stream connected (ADR-016). The background path uses a
file-backed SQLite engine (own connections per session) + fake pipeline deps so it exercises real
persistence without the heavy model or shared-connection races."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from portfolio_demo_identity import DEMO_AGENCY_ID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from fraudlens_backend.db.models import (
    Agency,
    Alert,
    AnalysisResult,
    AnalysisRun,
    JobStatus,
    ModelTrainingRun,
    ModelTrigger,
    ModelVersion,
    ModelVersionStatus,
    RagRetrieval,
    RunStatus,
    SarDraft,
    SarStatus,
    Severity,
    TrainingDataset,
    Transaction,
)
from fraudlens_backend.main import create_app
from fraudlens_backend.pipeline_wiring import RunManager
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskBand

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


async def _seed_completed_run(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    agency_id: uuid.UUID,
    with_result: bool = True,
    with_alert: bool = False,
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
                RagRetrieval(
                    agency_id=agency_id,
                    run_id=run.id,
                    query="structuring near threshold",
                    top_k=1,
                    chunks=[
                        {
                            "chunk_id": "fincen-structuring::0",
                            "doc_id": "fincen-structuring",
                            "citation": "31 CFR 1010.314",
                            "title": "Structuring",
                            "source": "FinCEN",
                            "text": "No person shall structure a transaction.",
                            "score": 0.98,
                        }
                    ],
                    rag_version="rag-test",
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
        if with_alert:
            session.add(
                Alert(
                    agency_id=agency_id,
                    transaction_id=transaction.id,
                    run_id=run.id,
                    severity=Severity.HIGH,
                    review_flags=[],
                )
            )
        await session.commit()
        return run.id


def _sse_events(body: str) -> list[str]:
    """Extract the ordered `event:` names from an SSE response body."""
    names: list[str] = []
    for frame in body.split("\n\n"):
        for line in frame.splitlines():
            if line.startswith("event: "):
                names.append(line[len("event: ") :])
    return names


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

"""Pipeline alert raising, PDF storage, missing-resource, and end-to-end triage tests."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from portfolio_demo_identity import DEMO_AGENCY_ID, DEMO_BYPASS_USER_ID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from storage_fakes import (
    _REVIEWER_ID,
    _client,
    _demo_app,
    _seed_alert,
)

from fraudlens_backend.db.models import (
    Alert,
    AlertAction,
    AlertStatus,
    AnalysisResult,
    AuditLog,
    SarDraft,
    SarStatus,
    Severity,
    SystemConfig,
    TrainingLabel,
)
from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    ModelRegistryRepository,
    SarDraftRepository,
)
from fraudlens_backend.db.repositories.alerts import load_label_maturity_days
from fraudlens_backend.pipeline_wiring import PipelineRunStore
from fraudlens_backend.sar.pdf import generate_sar_pdf
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskBand
from fraudlens_ml.pipeline import AlertRecord


async def test_raise_alert_persists_computed_review_flags(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(
        db_sessionmaker, with_sar=True, sar_status=SarStatus.FAILED
    )  # seeds the run + a failed SAR
    # The seeded run has a failed SAR but no result row; add a confident result so only the
    # critical-band + sar-unavailable flags fire (not low-confidence).
    async with db_sessionmaker() as session:
        session.add(
            AnalysisResult(
                agency_id=DEMO_AGENCY_ID,
                run_id=ids["run_id"],
                fraud_probability=0.97,
                shap_values={},
                top_features=[],
                rule_hits=[],
                combined_score=0.9,
                risk_band=RiskBand.CRITICAL,
                model_version="v-test",
            )
        )
        await session.commit()
    async with db_sessionmaker() as session:
        store = PipelineRunStore(
            session=session,
            run_id=ids["run_id"],
            transaction_id=ids["transaction_id"],
            analysis=AnalysisRunRepository(session, DEMO_AGENCY_ID),
            registry=ModelRegistryRepository(session),
            sar=SarDraftRepository(session, DEMO_AGENCY_ID),
            review_low_confidence_margin=0.1,
        )
        await store.raise_alert(AlertRecord(severity="critical", risk_band=RiskBand.CRITICAL))
    async with db_sessionmaker() as session:
        alerts = (
            (await session.execute(select(Alert).where(Alert.run_id == ids["run_id"])))
            .scalars()
            .all()
        )
    raised = [a for a in alerts if a.severity is Severity.CRITICAL]
    assert len(raised) == 1
    assert raised[0].status is AlertStatus.PENDING_REVIEW
    flags = {item["flag"] for item in raised[0].review_flags}
    assert flags == {"critical_risk_band", "sar_unavailable"}


async def test_raise_alert_without_review_flags_stays_open(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    async with db_sessionmaker() as session:
        repo = AnalysisRunRepository(session, DEMO_AGENCY_ID)
        alert = await repo.raise_alert(
            run_id=ids["run_id"],
            transaction_id=ids["transaction_id"],
            severity=Severity.HIGH,
            review_flags=[],
        )
        await session.commit()
        assert alert.status is AlertStatus.OPEN


async def test_load_label_maturity_days_defaults_and_overrides(
    db_session: AsyncSession,
) -> None:
    assert await load_label_maturity_days(db_session) == 30  # default when unset
    db_session.add(SystemConfig(agency_id=None, key="labelMaturityDays", value=14))
    await db_session.flush()
    assert await load_label_maturity_days(db_session) == 14  # global override honored


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (21, 21),  # plain int
        ("7", 7),  # numeric string coerces
        ("not-an-int", 30),  # un-coercible string → default
        (True, 30),  # bool is never a valid day count → default
        (3.5, 3),  # float truncates to int
        ({}, 30),  # non-scalar JSON → default
    ],
)
async def test_load_label_maturity_days_value_coercion(
    db_session: AsyncSession, value: object, expected: int
) -> None:
    db_session.add(SystemConfig(agency_id=None, key="labelMaturityDays", value=value))
    await db_session.flush()
    assert await load_label_maturity_days(db_session) == expected


async def test_load_label_maturity_days_db_error_falls_back() -> None:
    class _BoomSession:
        async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("db down")

    assert await load_label_maturity_days(_BoomSession()) == 30  # type: ignore[arg-type]


class _RecordingStorage:
    """A StorageBackend stub recording puts (and optionally always raising) for the PDF task."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.puts: list[str] = []

    def put(self, key: str, data: bytes) -> str:
        if self.fail:
            raise RuntimeError("storage unavailable")
        self.puts.append(key)
        return f"file:///{key}"

    def get(self, key: str) -> bytes:  # pragma: no cover - unused by the PDF task
        raise NotImplementedError


async def test_generate_sar_pdf_missing_draft_returns_false(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    storage = _RecordingStorage()
    ok = await generate_sar_pdf(
        sessionmaker=db_sessionmaker,
        storage=storage,
        agency_id=DEMO_AGENCY_ID,
        draft_id=uuid.uuid4(),
        max_attempts=3,
    )
    assert ok is False
    assert storage.puts == []


async def test_generate_sar_pdf_is_idempotent_when_already_generated(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    async with db_sessionmaker() as session:
        draft = await session.get(SarDraft, ids["sar_id"])
        assert draft is not None
        draft.pdf_blob_url = "file:///already/there.pdf"
        await session.commit()
    storage = _RecordingStorage()
    ok = await generate_sar_pdf(
        sessionmaker=db_sessionmaker,
        storage=storage,
        agency_id=DEMO_AGENCY_ID,
        draft_id=ids["sar_id"],
        max_attempts=3,
    )
    assert ok is True
    assert storage.puts == []  # nothing re-stored — idempotent re-entry


async def test_generate_sar_pdf_retries_then_gives_up(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    storage = _RecordingStorage(fail=True)  # every store attempt raises
    ok = await generate_sar_pdf(
        sessionmaker=db_sessionmaker,
        storage=storage,
        agency_id=DEMO_AGENCY_ID,
        draft_id=ids["sar_id"],
        max_attempts=2,
    )
    assert ok is False  # bounded retries exhausted; approval is unaffected
    async with db_sessionmaker() as session:
        draft = await session.get(SarDraft, ids["sar_id"])
        assert draft is not None
        assert draft.pdf_blob_url is None


async def test_action_on_missing_alert_returns_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{uuid.uuid4()}/actions", json={"action": "comment", "note": "x"}
        )
    assert resp.status_code == 404
    assert resp.json()["code"] == "alert_not_found"


async def test_sar_review_on_missing_alert_returns_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{uuid.uuid4()}/sar/review",
            json={"decision": "reject", "reason": "n/a"},
        )
    assert resp.status_code == 404
    assert resp.json()["code"] == "alert_not_found"


async def test_alert_detail_includes_action_history(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "comment", "note": "looking into this"},
        )
        detail = await client.get(f"/api/v1/alerts/{ids['alert_id']}")
    actions = detail.json()["actions"]
    assert len(actions) == 1
    assert actions[0]["action"] == "comment"
    assert actions[0]["actorId"] == str(DEMO_BYPASS_USER_ID)


async def test_triage_high_risk_alert_end_to_end(
    make_settings: Callable[..., AppSettings],
    file_db: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    engine, sm = file_db
    ids = await _seed_alert(sm, status=AlertStatus.OPEN, sar_status=SarStatus.DRAFT)
    storage_dir = tmp_path / "e2e-artifacts"
    app = _demo_app(make_settings, engine, sm, storage_local_dir=str(storage_dir))
    async with _client(app) as client:
        # 1. the high-risk alert sits in the open queue
        listed = await client.get("/api/v1/alerts", params={"status": "open"})
        assert [a["alertId"] for a in listed.json()["alerts"]] == [str(ids["alert_id"])]
        # 2. opening the detail surfaces the draft SAR
        detail = await client.get(f"/api/v1/alerts/{ids['alert_id']}")
        assert detail.json()["sarDraft"]["status"] == "draft"
        # 3. assign to the reviewer → in_review
        assigned = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "assign", "assigneeId": str(_REVIEWER_ID)},
        )
        assert assigned.json()["status"] == "in_review"
        # 4. reviewer approves the SAR → approved + deferred PDF
        approved = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/sar/review", json={"decision": "approve"}
        )
        assert approved.json()["status"] == "approved"
        # 5. resolve as confirmed fraud → resolved + a training label
        resolved = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={
                "action": "resolve",
                "label": "confirmed_fraud",
                "note": "Confirmed structuring.",
            },
        )
        assert resolved.json()["status"] == "resolved"
    # Terminal: alert resolved+assigned, SAR approved with a PDF, label written, fully audited.
    async with sm() as session:
        alert = await session.get(Alert, ids["alert_id"])
        assert alert is not None
        assert alert.status is AlertStatus.RESOLVED
        assert alert.assigned_to == _REVIEWER_ID
        draft = await session.get(SarDraft, ids["sar_id"])
        assert draft is not None
        assert draft.status is SarStatus.APPROVED
        assert draft.pdf_blob_url is not None
        label = (
            await session.execute(
                select(TrainingLabel).where(TrainingLabel.run_id == ids["run_id"])
            )
        ).scalar_one()
        assert label.label.value == "confirmed_fraud"
        assert label.created_by == DEMO_BYPASS_USER_ID
        alert_actions = (
            (
                await session.execute(
                    select(AlertAction).where(AlertAction.alert_id == ids["alert_id"])
                )
            )
            .scalars()
            .all()
        )
        assert {a.action.value for a in alert_actions} == {"assign", "resolve"}
        audits = {a.action for a in (await session.execute(select(AuditLog))).scalars().all()}
        assert audits == {"alert.assign", "sar.approve", "alert.resolve"}
    pdf_path = storage_dir / "sar" / str(DEMO_AGENCY_ID) / f"{ids['sar_id']}.pdf"
    assert pdf_path.read_bytes().startswith(b"%PDF-1.4")

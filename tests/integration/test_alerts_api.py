"""Alerts & review-workflow API tests (plan §5.4, §10.4, §16 Phase 9; endpoints 9-12). Covers the
acceptance criteria: list/detail tenant scoping (cross-tenant → 404), legal/illegal status
transitions (409), assign/comment/escalate/resolve/dismiss, cross-tenant assignee → 403, PHI-masked
notes, resolve writing a training label, SAR approve/edit/reject (reason required) with a deferred
mock PDF, fail-closed acting-user enforcement, and that the pipeline alert-raise persists computed
review flags. The deferred-PDF test uses a file-backed SQLite engine so the background session
uses a separate connection (mirroring the investigations suite)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fraudlens_backend.api.deps import get_tenant
from fraudlens_backend.db.models import (
    Agency,
    Alert,
    AlertAction,
    AlertStatus,
    AnalysisResult,
    AnalysisRun,
    AuditLog,
    Base,
    RunStatus,
    SarDraft,
    SarStatus,
    Severity,
    SystemConfig,
    TrainingLabel,
    Transaction,
    User,
    UserRole,
)
from fraudlens_backend.db.repositories import (
    AnalysisRunRepository,
    ModelRegistryRepository,
    SarDraftRepository,
)
from fraudlens_backend.db.repositories.alerts import load_label_maturity_days
from fraudlens_backend.demo import DEMO_AGENCY_ID, DEMO_USER_ID
from fraudlens_backend.main import create_app
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.pipeline_wiring import PipelineRunStore
from fraudlens_backend.sar.pdf import generate_sar_pdf
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskBand
from fraudlens_ml.pipeline import AlertRecord

_REVIEWER_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
_OTHER_AGENCY_ID = uuid.UUID("66666666-6666-4666-8666-666666666666")
_OTHER_USER_ID = uuid.UUID("77777777-7777-4777-8777-777777777777")


def _client(app: object) -> httpx.AsyncClient:
    """An AsyncClient driving the ASGI app in-process (same loop as the DB)."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _demo_app(
    make_settings: Callable[..., AppSettings], engine: AsyncEngine, sm: Any, **kw: Any
) -> Any:
    """Build a dev-bypass app (tenant → demo agency, actor → demo analyst) wired to the test DB."""
    app = create_app(make_settings(environment="dev", auth_dev_bypass=True, **kw))
    app.state.db_engine = engine  # type: ignore[attr-defined]
    app.state.db_sessionmaker = sm  # type: ignore[attr-defined]
    return app


async def _ensure_identities(session: AsyncSession) -> None:
    """Insert the demo + other agencies and their users (idempotent within a test)."""
    if await session.get(Agency, DEMO_AGENCY_ID) is None:
        session.add(Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo-alerts"))
        session.add(Agency(id=_OTHER_AGENCY_ID, name="Other", slug="other-alerts"))
        session.add(
            User(
                id=DEMO_USER_ID,
                agency_id=DEMO_AGENCY_ID,
                email="analyst@demo-agency.test",
                display_name="Demo Analyst",
                role=UserRole.ANALYST,
            )
        )
        session.add(
            User(
                id=_REVIEWER_ID,
                agency_id=DEMO_AGENCY_ID,
                email="reviewer@demo-agency.test",
                display_name="Demo Reviewer",
                role=UserRole.REVIEWER,
            )
        )
        session.add(
            User(
                id=_OTHER_USER_ID,
                agency_id=_OTHER_AGENCY_ID,
                email="analyst@other.test",
                display_name="Other Analyst",
                role=UserRole.ANALYST,
            )
        )


async def _seed_alert(
    sm: async_sessionmaker[AsyncSession],
    *,
    agency_id: uuid.UUID = DEMO_AGENCY_ID,
    status: AlertStatus = AlertStatus.OPEN,
    with_sar: bool = True,
    sar_status: SarStatus = SarStatus.DRAFT,
    review_flags: list[dict[str, str]] | None = None,
    external_id: str | None = None,
) -> dict[str, uuid.UUID]:
    """Seed an alert (+ its transaction, run, and optional SAR draft); return the relevant ids."""
    async with sm() as session:
        await _ensure_identities(session)
        transaction = Transaction(
            agency_id=agency_id,
            external_id=external_id or f"T-{uuid.uuid4().hex[:8]}",
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
            risk_score=0.82,
            risk_band=RiskBand.HIGH,
            model_version="v-test",
        )
        session.add(run)
        await session.flush()
        sar_id: uuid.UUID | None = None
        if with_sar:
            draft = SarDraft(
                agency_id=agency_id,
                run_id=run.id,
                model_id="mock",
                prompt_version="sar-v1",
                prompt_hash="h",
                content="Original SAR narrative.",
                structured={},
                citations=[{"citation": "31 CFR 1010.314"}],
                status=sar_status,
            )
            session.add(draft)
            await session.flush()
            sar_id = draft.id
        alert = Alert(
            agency_id=agency_id,
            transaction_id=transaction.id,
            run_id=run.id,
            status=status,
            severity=Severity.HIGH,
            review_flags=review_flags or [],
        )
        session.add(alert)
        await session.flush()
        ids = {
            "alert_id": alert.id,
            "run_id": run.id,
            "transaction_id": transaction.id,
        }
        if sar_id is not None:
            ids["sar_id"] = sar_id
        await session.commit()
        return ids


# --------------------------------------------------------------------------------------------------
# List + detail
# --------------------------------------------------------------------------------------------------


async def test_list_alerts_scoped_and_status_filter(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_alert(db_sessionmaker, status=AlertStatus.OPEN, external_id="A1")
    await _seed_alert(db_sessionmaker, status=AlertStatus.RESOLVED, external_id="A2")
    await _seed_alert(db_sessionmaker, agency_id=_OTHER_AGENCY_ID, external_id="A3")  # cross-tenant
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        all_resp = await client.get("/api/v1/alerts")
        open_resp = await client.get("/api/v1/alerts", params={"status": "open"})
    assert all_resp.status_code == 200
    assert len(all_resp.json()["alerts"]) == 2  # only the demo agency's alerts
    assert [a["status"] for a in open_resp.json()["alerts"]] == ["open"]


async def test_get_alert_detail_surfaces_sar_and_flags(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    flags = [{"flag": "critical_risk_band", "reason": "Risk band is critical."}]
    ids = await _seed_alert(db_sessionmaker, review_flags=flags)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/alerts/{ids['alert_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["alert"]["reviewFlags"][0]["flag"] == "critical_risk_band"
    assert body["sarDraft"]["citations"][0]["citation"] == "31 CFR 1010.314"
    assert body["actions"] == []


async def test_get_alert_cross_tenant_returns_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker, agency_id=_OTHER_AGENCY_ID)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/alerts/{ids['alert_id']}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "alert_not_found"


# --------------------------------------------------------------------------------------------------
# Actions (assign / comment / escalate / resolve / dismiss)
# --------------------------------------------------------------------------------------------------


async def test_assign_moves_to_in_review_and_audits(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "assign", "assigneeId": str(_REVIEWER_ID)},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "in_review"
    assert body["assignedTo"] == str(_REVIEWER_ID)
    async with db_sessionmaker() as session:
        action = (
            await session.execute(
                select(AlertAction).where(AlertAction.alert_id == ids["alert_id"])
            )
        ).scalar_one()
        assert action.action.value == "assign"
        assert action.actor_id == DEMO_USER_ID  # the dev-bypass acting user
        assert action.to_status == "in_review"
        audit = (
            await session.execute(select(AuditLog).where(AuditLog.action == "alert.assign"))
        ).scalar_one()
        assert audit.resource_id == str(ids["alert_id"])
        assert audit.actor_id == DEMO_USER_ID
        assert audit.meta["assigneeId"] == str(_REVIEWER_ID)


async def test_assign_cross_tenant_assignee_returns_403(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "assign", "assigneeId": str(_OTHER_USER_ID)},
        )
    assert resp.status_code == 403
    assert resp.json()["code"] == "assignee_not_in_agency"


async def test_resolve_writes_training_label(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "resolve", "label": "confirmed_fraud"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"
    async with db_sessionmaker() as session:
        label = (
            await session.execute(
                select(TrainingLabel).where(TrainingLabel.run_id == ids["run_id"])
            )
        ).scalar_one()
        assert label.label.value == "confirmed_fraud"
        assert label.created_by == DEMO_USER_ID
        assert label.transaction_id == ids["transaction_id"]
        assert label.matured_at is not None  # a future maturity is stamped for the retrain job


async def test_resolve_without_label_is_422(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions", json={"action": "resolve"}
        )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    ("action", "expected"),
    [("escalate", "in_review"), ("dismiss", "dismissed")],
)
async def test_escalate_and_dismiss_transitions(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    action: str,
    expected: str,
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions", json={"action": action}
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == expected


async def test_illegal_transition_on_terminal_alert_is_409(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker, status=AlertStatus.RESOLVED)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions", json={"action": "comment", "note": "hi"}
        )
    assert resp.status_code == 409
    assert resp.json()["code"] == "invalid_alert_transition"


async def test_comment_note_is_phi_masked(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions",
            json={"action": "comment", "note": "reach me at evil@example.com about this"},
        )
    assert resp.status_code == 200
    async with db_sessionmaker() as session:
        action = (
            await session.execute(
                select(AlertAction).where(AlertAction.alert_id == ids["alert_id"])
            )
        ).scalar_one()
    assert "evil@example.com" not in (action.note or "")  # PHI-shaped span masked
    assert "[REDACTED_EMAIL]" in (action.note or "")


async def test_acting_user_required_fails_closed(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    # Simulate a verified token that carries no subject (no acting user).
    app.dependency_overrides[get_tenant] = lambda: TenantContext(
        agency_id=str(DEMO_AGENCY_ID), user_id=None
    )
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/actions", json={"action": "comment"}
        )
    assert resp.status_code == 401
    assert resp.json()["code"] == "acting_user_required"


# --------------------------------------------------------------------------------------------------
# SAR review (approve / reject / edit)
# --------------------------------------------------------------------------------------------------


async def test_sar_reject_requires_reason_is_422(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/sar/review", json={"decision": "reject"}
        )
    assert resp.status_code == 422


async def test_sar_reject_sets_rejected(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/sar/review",
            json={"decision": "reject", "reason": "Insufficient evidence."},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    async with db_sessionmaker() as session:
        draft = await session.get(SarDraft, ids["sar_id"])
        assert draft is not None
        assert draft.status is SarStatus.REJECTED
        assert draft.reviewed_by == DEMO_USER_ID


async def test_sar_edit_creates_new_reviewed_version(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/sar/review",
            json={"decision": "edit", "editedContent": "Analyst-authored narrative."},
        )
        detail = await client.get(f"/api/v1/alerts/{ids['alert_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 2  # a new version, not an overwrite
    assert body["status"] == "reviewed"
    assert body["content"] == "Analyst-authored narrative."
    assert detail.json()["sarDraft"]["version"] == 2  # detail surfaces the latest version


async def test_sar_review_without_draft_is_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker, with_sar=False)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/sar/review", json={"decision": "approve"}
        )
    assert resp.status_code == 404
    assert resp.json()["code"] == "sar_draft_not_found"


async def test_sar_review_on_decided_draft_is_409(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed_alert(db_sessionmaker, sar_status=SarStatus.APPROVED)
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/sar/review", json={"decision": "approve"}
        )
    assert resp.status_code == 409
    assert resp.json()["code"] == "invalid_sar_transition"


# --------------------------------------------------------------------------------------------------
# SAR approve → deferred PDF (file-backed DB so the background session has its own connection)
# --------------------------------------------------------------------------------------------------


@pytest.fixture
async def file_db(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    """A file-backed SQLite engine so background + request sessions use separate connections."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'alerts.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine, async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_sar_approve_generates_pdf_deferred(
    make_settings: Callable[..., AppSettings],
    file_db: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    engine, sm = file_db
    ids = await _seed_alert(sm)
    storage_dir = tmp_path / "artifacts"
    app = _demo_app(make_settings, engine, sm, storage_local_dir=str(storage_dir))
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/alerts/{ids['alert_id']}/sar/review", json={"decision": "approve"}
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    # The deferred PDF task runs within the ASGI response lifecycle, so it has completed here.
    async with sm() as session:
        draft = await session.get(SarDraft, ids["sar_id"])
        assert draft is not None
        assert draft.status is SarStatus.APPROVED
        assert draft.pdf_blob_url is not None
    pdf_path = storage_dir / "sar" / str(DEMO_AGENCY_ID) / f"{ids['sar_id']}.pdf"
    assert pdf_path.is_file()
    assert pdf_path.read_bytes().startswith(b"%PDF-1.4")


# --------------------------------------------------------------------------------------------------
# Pipeline alert-raise persists computed review flags (auto-create above threshold)
# --------------------------------------------------------------------------------------------------


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
    flags = {item["flag"] for item in raised[0].review_flags}
    assert flags == {"critical_risk_band", "sar_unavailable"}


# --------------------------------------------------------------------------------------------------
# load_label_maturity_days resolution (DB tunable with safe default)
# --------------------------------------------------------------------------------------------------


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


# --------------------------------------------------------------------------------------------------
# Deferred SAR-PDF task: idempotency, missing draft, and retry-exhaustion (best-effort, no crash)
# --------------------------------------------------------------------------------------------------


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


# --------------------------------------------------------------------------------------------------
# Missing-alert paths + action-history projection
# --------------------------------------------------------------------------------------------------


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
    assert actions[0]["actorId"] == str(DEMO_USER_ID)

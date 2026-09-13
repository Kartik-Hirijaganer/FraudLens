"""SAR review lifecycle and deferred PDF integration tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from portfolio_demo_identity import DEMO_AGENCY_ID, DEMO_BYPASS_USER_ID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from storage_fakes import (
    _client,
    _demo_app,
    _seed_alert,
)

from fraudlens_backend.db.models import (
    AuditLog,
    SarDraft,
    SarStatus,
)
from fraudlens_backend.settings import AppSettings


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
        assert draft.reviewed_by == DEMO_BYPASS_USER_ID


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
            json={
                "decision": "edit",
                "editedContent": "Analyst-authored narrative from evil@example.com.",
            },
        )
        detail = await client.get(f"/api/v1/alerts/{ids['alert_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 2  # a new version, not an overwrite
    assert body["status"] == "reviewed"
    assert body["content"] == "Analyst-authored narrative from [REDACTED_EMAIL]."
    assert detail.json()["sarDraft"]["version"] == 2  # detail surfaces the latest version
    async with db_sessionmaker() as session:
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.agency_id == DEMO_AGENCY_ID,
                    AuditLog.action == "phi_mask",
                    AuditLog.resource_type == "sar_draft",
                )
            )
        ).scalar_one()
    assert audit.meta == {
        "source": "sar.review_edit",
        "maskedCount": "1",
        "categories": "email:1",
    }


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

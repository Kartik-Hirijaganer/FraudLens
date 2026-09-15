"""Investigation SAR regeneration state and tenant-isolation API tests."""

from __future__ import annotations

from collections.abc import Callable

from investigation_fakes import (
    _OTHER_AGENCY_ID,
    _client,
    _demo_app,
    _seed_completed_run,
    _seed_run_for_regen,
)
from portfolio_demo_identity import DEMO_AGENCY_ID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from fraudlens_backend.db.models import (
    SarDraft,
    SarStatus,
)
from fraudlens_backend.settings import AppSettings


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

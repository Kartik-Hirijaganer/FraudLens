"""Transaction API tests (plan §5.4 / §16 Phase 3): ingest single/batch/CSV with masked-only
storage, dedup (409), validation (422), size caps (413/415), dryRun, partial-accept, keyset
listing, and cross-tenant isolation (404, no existence leak). Uses httpx + ASGITransport so
requests share the event loop with the async SQLite engine (mirrors test_agencies_db.py)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from portfolio_demo_identity import DEMO_AGENCY_ID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fraudlens_backend.api.deps import AccessClaims, TokenVerifier, get_token_verifier
from fraudlens_backend.db.models import Agency, JobExecution
from fraudlens_backend.main import create_app
from fraudlens_backend.settings import AppSettings

AUTH = {"Authorization": "Bearer test-token"}
CSV_HEADER = "externalId,amount,currency,occurredAt,originAccount,destAccount,channel,country"


def _txn(**overrides: Any) -> dict[str, Any]:
    """A valid camelCase ingest body with per-test overrides."""
    body: dict[str, Any] = {
        "externalId": "T1",
        "amount": "100.50",
        "currency": "usd",
        "occurredAt": "2026-01-01T00:00:00+00:00",
        "originAccount": "4111111111111111",
        "destAccount": "987654321",
        "channel": "wire",
        "country": "us",
    }
    body.update(overrides)
    return body


def _build_app(settings: AppSettings, engine: AsyncEngine, sm: async_sessionmaker[AsyncSession]):
    """Build an app wired to the in-memory test engine/sessionmaker."""
    app = create_app(settings)
    app.state.db_engine = engine
    app.state.db_sessionmaker = sm
    return app


def _accept(agency_id: str) -> Callable[[], TokenVerifier]:
    """Override factory: a verifier accepting any token as the given agency claim."""
    return lambda: lambda _token: AccessClaims(agency_id=agency_id)


def _client(app: object) -> httpx.AsyncClient:
    """An AsyncClient driving the ASGI app in-process (same loop as the DB)."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _seed_agency(sm: async_sessionmaker[AsyncSession], agency: Agency) -> None:
    """Insert + commit an agency (FK target for transactions)."""
    async with sm() as session:
        session.add(agency)
        await session.commit()


def _demo_app(
    make_settings: Callable[..., AppSettings],
    engine: AsyncEngine,
    sm: async_sessionmaker[AsyncSession],
    **settings_overrides: Any,
):
    """Build a dev-bypass app whose tenant resolves to the seeded demo agency."""
    settings = make_settings(environment="dev", auth_dev_bypass=True, **settings_overrides)
    return _build_app(settings, engine, sm)


async def test_ingest_single_masks_and_returns_201(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post("/api/v1/transactions", json=_txn())
    assert resp.status_code == 201
    body = resp.json()
    assert body["currency"] == "USD"  # normalized
    assert body["originAccount"].endswith("1111")
    assert "4111111111111111" not in body["originAccount"]  # masked-only
    assert body["riskBand"] is None


async def test_ingest_accepts_the_documented_transaction_text_limit(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The 128-character request maximum must also fit in the persistence schema."""
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    boundary = "SYNTH-" + ("A" * 122)
    async with _client(app) as client:
        accepted = await client.post(
            "/api/v1/transactions",
            json=_txn(
                externalId="BOUNDARY",
                originAccount=boundary,
                destAccount=boundary,
                channel="C" * 128,
            ),
        )
        rejected = await client.post(
            "/api/v1/transactions",
            json=_txn(externalId="TOO-LONG", originAccount=boundary + "X"),
        )

    assert accepted.status_code == 201
    assert len(accepted.json()["originAccount"]) == 128
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "validation_error"


async def test_ingest_duplicate_returns_409(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        assert (await client.post("/api/v1/transactions", json=_txn())).status_code == 201
        dup = await client.post("/api/v1/transactions", json=_txn())
    assert dup.status_code == 409
    assert dup.json()["code"] == "duplicate_external_id"


async def test_ingest_invalid_amount_is_422(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post("/api/v1/transactions", json=_txn(amount="-5"))
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


async def test_ingest_future_date_is_422_with_field(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/transactions", json=_txn(occurredAt="2999-01-01T00:00:00+00:00")
        )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "validation_error"
    assert body["details"][0]["field"] == "occurred_at"


async def test_no_token_fails_closed_401(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    app = _build_app(make_settings(), db_engine, db_sessionmaker)  # no bypass, no verifier
    async with _client(app) as client:
        resp = await client.post("/api/v1/transactions", json=_txn())
    assert resp.status_code == 401


async def test_auditor_can_list_transactions_but_not_ingest(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker, auth_dev_bypass_role="auditor")
    async with _client(app) as client:
        listing = await client.get("/api/v1/transactions")
        created = await client.post("/api/v1/transactions", json=_txn())
        uploaded = await client.post(
            "/api/v1/transactions/upload",
            content=f"{CSV_HEADER}\nT1,1,USD,2026-01-01T00:00:00Z,a,b,wire,US",
        )
    assert listing.status_code == 200
    assert created.status_code == 403
    assert uploaded.status_code == 403
    assert created.json()["code"] == "role_permission_required"


async def test_batch_partial_accept(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    payload = {
        "transactions": [
            _txn(externalId="OK1"),
            _txn(externalId="BAD", amount="-1"),
            _txn(externalId="OK2"),
        ]
    }
    async with _client(app) as client:
        resp = await client.post("/api/v1/transactions/batch", json=payload)
    body = resp.json()
    assert resp.status_code == 200
    assert (body["accepted"], body["rejected"]) == (2, 1)
    assert body["sampleErrors"][0]["externalId"] == "BAD"
    assert body["sampleErrors"][0]["code"] == "validation_error"


async def test_batch_dry_run_persists_nothing(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/transactions/batch",
            json={"dryRun": True, "transactions": [_txn(externalId="D1")]},
        )
        listing = await client.get("/api/v1/transactions")
    assert resp.json()["accepted"] == 1
    assert resp.json()["transactions"] == []
    assert listing.json()["transactions"] == []  # nothing persisted


async def test_batch_too_large_is_413(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker, ingest_max_batch_size=1)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/transactions/batch",
            json={"transactions": [_txn(externalId="A"), _txn(externalId="B")]},
        )
    assert resp.status_code == 413
    assert resp.json()["code"] == "batch_too_large"


async def test_csv_upload_partial_accept_records_job(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    csv_body = (
        f"{CSV_HEADER}\n"
        "C1,10.00,USD,2026-01-02T00:00:00+00:00,4111111111111111,55667788,card,US\n"
        "C2,bad,USD,2026-01-02T00:00:00+00:00,1,2,card,US\n"
    )
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/transactions/upload", content=csv_body, headers={"content-type": "text/csv"}
        )
    body = resp.json()
    assert resp.status_code == 202
    assert (body["accepted"], body["rejected"]) == (1, 1)
    assert body["jobId"]
    async with db_sessionmaker() as session:
        jobs = (await session.execute(select(func.count()).select_from(JobExecution))).scalar_one()
    assert jobs == 1


async def test_csv_oversize_is_413(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker, ingest_csv_max_bytes=32)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/transactions/upload",
            content=f"{CSV_HEADER}\nX,1,USD,2026-01-01T00:00:00+00:00,1,2,c,US\n",
            headers={"content-type": "text/csv"},
        )
    assert resp.status_code == 413
    assert resp.json()["code"] == "payload_too_large"


async def test_csv_too_many_rows_is_413(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker, ingest_csv_max_rows=1)
    rows = "".join(f"R{i},1.00,USD,2026-01-01T00:00:00+00:00,1,2,c,US\n" for i in range(2))
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/transactions/upload",
            content=f"{CSV_HEADER}\n{rows}",
            headers={"content-type": "text/csv"},
        )
    assert resp.status_code == 413
    assert resp.json()["code"] == "too_many_rows"


async def test_csv_wrong_content_type_is_415(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post("/api/v1/transactions/upload", json={"not": "csv"})
    assert resp.status_code == 415
    assert resp.json()["code"] == "unsupported_content_type"


async def test_csv_empty_is_422(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/transactions/upload",
            content=f"{CSV_HEADER}\n",
            headers={"content-type": "text/csv"},
        )
    assert resp.status_code == 422
    assert resp.json()["code"] == "empty_payload"


async def test_list_paginates_and_detail_404_cross_tenant(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        for i in range(3):
            await client.post("/api/v1/transactions", json=_txn(externalId=f"L{i}"))
        page1 = (await client.get("/api/v1/transactions?limit=2")).json()
        assert len(page1["transactions"]) == 2
        assert page1["nextCursor"]
        page2 = (
            await client.get(f"/api/v1/transactions?limit=2&cursor={page1['nextCursor']}")
        ).json()
        assert len(page2["transactions"]) == 1
        # detail round-trips, and an unknown id is 404 (same body as cross-tenant).
        tid = page1["transactions"][0]["transactionId"]
        assert (await client.get(f"/api/v1/transactions/{tid}")).status_code == 200
        missing = await client.get(f"/api/v1/transactions/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "transaction_not_found"


async def test_cross_tenant_isolation(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    agency_a = Agency(id=uuid.uuid4(), name="A", slug="a")
    agency_b = Agency(id=uuid.uuid4(), name="B", slug="b")
    await _seed_agency(db_sessionmaker, agency_a)
    await _seed_agency(db_sessionmaker, agency_b)
    app = _build_app(make_settings(), db_engine, db_sessionmaker)

    app.dependency_overrides[get_token_verifier] = _accept(str(agency_a.id))  # type: ignore[attr-defined]
    async with _client(app) as client:
        created = await client.post(
            "/api/v1/transactions", json=_txn(externalId="A1"), headers=AUTH
        )
        tid = created.json()["transactionId"]

    app.dependency_overrides[get_token_verifier] = _accept(str(agency_b.id))  # type: ignore[attr-defined]
    async with _client(app) as client:
        listing = await client.get("/api/v1/transactions", headers=AUTH)
        detail = await client.get(f"/api/v1/transactions/{tid}", headers=AUTH)
    assert listing.json()["transactions"] == []  # B cannot see A's row
    assert detail.status_code == 404  # no existence leak

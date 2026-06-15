"""Cross-tenant isolation gate (plan §16 Phase 13, §6.4): the consolidated proof that one
agency can never see another's data and can never address another tenant by id. A claim-scoped
resource (a transaction) is invisible to a second agency (404, no existence leak), and a
path-addressed resource (the agency lookup) rejects a claim/path mismatch (403)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from fraudlens_backend.api.deps import TokenVerifier, get_token_verifier
from fraudlens_backend.db.models import Agency

AUTH = {"Authorization": "Bearer test-token"}


def _txn() -> dict[str, str]:
    """A valid camelCase ingest body (account identifiers are masked on storage)."""
    return {
        "externalId": "ISO-1",
        "amount": "100.50",
        "currency": "usd",
        "occurredAt": "2026-01-01T00:00:00+00:00",
        "originAccount": "4111111111111111",
        "destAccount": "987654321",
        "channel": "wire",
        "country": "us",
    }


async def _seed_agency(sm: async_sessionmaker[Any], agency: Agency) -> None:
    """Insert + commit an agency (the FK target for tenant-scoped rows)."""
    async with sm() as session:
        session.add(agency)
        await session.commit()


async def test_other_agency_cannot_see_a_transaction(
    make_security_app: Callable[..., Any],
    accept: Callable[..., Callable[[], TokenVerifier]],
    aclient: Callable[[Any], httpx.AsyncClient],
    db_sessionmaker: async_sessionmaker[Any],
) -> None:
    agency_a = Agency(id=uuid.uuid4(), name="A", slug="a")
    agency_b = Agency(id=uuid.uuid4(), name="B", slug="b")
    await _seed_agency(db_sessionmaker, agency_a)
    await _seed_agency(db_sessionmaker, agency_b)
    app = make_security_app()

    app.dependency_overrides[get_token_verifier] = accept(str(agency_a.id))
    async with aclient(app) as client:
        created = await client.post("/api/v1/transactions", json=_txn(), headers=AUTH)
        assert created.status_code == 201
        txn_id = created.json()["transactionId"]

    app.dependency_overrides[get_token_verifier] = accept(str(agency_b.id))
    async with aclient(app) as client:
        detail = await client.get(f"/api/v1/transactions/{txn_id}", headers=AUTH)
    assert detail.status_code == 404  # invisible to agency B — no existence leak


async def test_claim_cannot_address_another_tenant_by_path(
    make_security_app: Callable[..., Any],
    accept: Callable[..., Callable[[], TokenVerifier]],
    aclient: Callable[[Any], httpx.AsyncClient],
    db_sessionmaker: async_sessionmaker[Any],
) -> None:
    agency_a = Agency(id=uuid.uuid4(), name="A", slug="a")
    agency_b = Agency(id=uuid.uuid4(), name="B", slug="b")
    await _seed_agency(db_sessionmaker, agency_a)
    await _seed_agency(db_sessionmaker, agency_b)
    app = make_security_app()
    app.dependency_overrides[get_token_verifier] = accept(str(agency_a.id))

    async with aclient(app) as client:
        own = await client.get(f"/api/v1/agencies/{agency_a.id}", headers=AUTH)
        other = await client.get(f"/api/v1/agencies/{agency_b.id}", headers=AUTH)
    assert own.status_code == 200  # a tenant can read itself
    assert other.status_code == 403  # but never another tenant by path (claim/path mismatch)

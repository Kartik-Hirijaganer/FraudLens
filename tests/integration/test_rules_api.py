"""AML-rules API tests (plan §5.3 endpoint 14 / §16 Phase 4): agency-scoped CRUD with create
(201) + dedup (409), list/detail, partial PATCH that bumps the version and toggles enabled,
delete (204), fail-closed auth (401), request validation (422), and cross-tenant isolation
(404 / empty list, no existence leak). Uses httpx + ASGITransport like test_transactions_api."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fraudlens_backend.api.deps import AccessClaims, TokenVerifier, get_token_verifier
from fraudlens_backend.db.models import Agency
from fraudlens_backend.demo import DEMO_AGENCY_ID
from fraudlens_backend.main import create_app
from fraudlens_backend.settings import AppSettings

AUTH = {"Authorization": "Bearer test-token"}


def _rule(**overrides: Any) -> dict[str, Any]:
    """A valid camelCase rule-create body with per-test overrides."""
    body: dict[str, Any] = {
        "code": "custom_velocity",
        "name": "Custom velocity",
        "description": "Tighter velocity for this agency",
        "ruleType": "velocity",
        "params": {"windowHours": 12, "maxCount": 3},
        "severity": "high",
        "weight": "1.5",
        "enabled": True,
    }
    body.update(overrides)
    return body


def _build_app(settings: AppSettings, engine: AsyncEngine, sm: async_sessionmaker[AsyncSession]):
    """Build an app wired to the in-memory test engine/sessionmaker."""
    app = create_app(settings)
    app.state.db_engine = engine
    app.state.db_sessionmaker = sm
    return app


def _accept(
    agency_id: str,
    *,
    role: str = "admin",
    user_id: str = "22222222-2222-4222-8222-222222222222",
) -> Callable[[], TokenVerifier]:
    """Override factory: a verifier accepting any token as the given agency/role claim."""
    return lambda: lambda _token: AccessClaims(agency_id=agency_id, role=role, user_id=user_id)


def _client(app: object) -> httpx.AsyncClient:
    """An AsyncClient driving the ASGI app in-process (same loop as the DB)."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _seed_agency(sm: async_sessionmaker[AsyncSession], agency: Agency) -> None:
    """Insert + commit an agency (FK target for agency-scoped rules)."""
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


async def test_create_returns_201_at_version_1(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.post("/api/v1/rules", json=_rule())
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == "custom_velocity"
    assert body["ruleType"] == "velocity"
    assert body["severity"] == "high"
    assert body["version"] == 1
    assert body["agencyId"] == str(DEMO_AGENCY_ID)


async def test_duplicate_code_returns_409(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        assert (await client.post("/api/v1/rules", json=_rule())).status_code == 201
        dup = await client.post("/api/v1/rules", json=_rule())
    assert dup.status_code == 409
    assert dup.json()["code"] == "duplicate_rule_code"


async def test_list_returns_agency_rules_ordered_by_code(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        await client.post("/api/v1/rules", json=_rule(code="r2"))
        await client.post("/api/v1/rules", json=_rule(code="r1"))
        listing = await client.get("/api/v1/rules")
    assert [rule["code"] for rule in listing.json()["rules"]] == ["r1", "r2"]


async def test_get_detail_and_unknown_is_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        created = (await client.post("/api/v1/rules", json=_rule())).json()
        rule_id = created["ruleId"]
        assert (await client.get(f"/api/v1/rules/{rule_id}")).status_code == 200
        missing = await client.get(f"/api/v1/rules/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "rule_not_found"


async def test_patch_bumps_version_and_toggles_enabled(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        created = (await client.post("/api/v1/rules", json=_rule())).json()
        rule_id = created["ruleId"]
        patched = await client.patch(
            f"/api/v1/rules/{rule_id}", json={"enabled": False, "weight": "2.5"}
        )
    body = patched.json()
    assert patched.status_code == 200
    assert body["version"] == 2  # version bumped server-side
    assert body["enabled"] is False
    assert Decimal(body["weight"]) == Decimal("2.5")


async def test_patch_unknown_is_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        resp = await client.patch(f"/api/v1/rules/{uuid.uuid4()}", json={"enabled": False})
    assert resp.status_code == 404
    assert resp.json()["code"] == "rule_not_found"


async def test_delete_then_get_is_404(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        rule_id = (await client.post("/api/v1/rules", json=_rule())).json()["ruleId"]
        assert (await client.delete(f"/api/v1/rules/{rule_id}")).status_code == 204
        assert (await client.get(f"/api/v1/rules/{rule_id}")).status_code == 404
        assert (await client.delete(f"/api/v1/rules/{rule_id}")).status_code == 404


async def test_no_token_fails_closed_401(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    app = _build_app(make_settings(), db_engine, db_sessionmaker)  # no bypass, no verifier
    async with _client(app) as client:
        resp = await client.post("/api/v1/rules", json=_rule())
    assert resp.status_code == 401


async def test_auditor_can_read_rules_but_not_manage_them(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker, auth_dev_bypass_role="auditor")
    async with _client(app) as client:
        listing = await client.get("/api/v1/rules")
        created = await client.post("/api/v1/rules", json=_rule())
    assert listing.status_code == 200
    assert created.status_code == 403
    assert created.json()["code"] == "role_permission_required"


async def test_invalid_body_is_422(
    make_settings: Callable[..., AppSettings],
    db_engine: AsyncEngine,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agency(db_sessionmaker, Agency(id=DEMO_AGENCY_ID, name="Demo", slug="demo"))
    app = _demo_app(make_settings, db_engine, db_sessionmaker)
    async with _client(app) as client:
        bad_type = await client.post("/api/v1/rules", json=_rule(ruleType="bogus"))
        bad_weight = await client.post("/api/v1/rules", json=_rule(weight="0"))
    assert bad_type.status_code == 422
    assert bad_weight.status_code == 422


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
        created = await client.post("/api/v1/rules", json=_rule(), headers=AUTH)
        rule_id = created.json()["ruleId"]

    app.dependency_overrides[get_token_verifier] = _accept(str(agency_b.id))  # type: ignore[attr-defined]
    async with _client(app) as client:
        listing = await client.get("/api/v1/rules", headers=AUTH)
        detail = await client.get(f"/api/v1/rules/{rule_id}", headers=AUTH)
        deleted = await client.delete(f"/api/v1/rules/{rule_id}", headers=AUTH)
    assert listing.json()["rules"] == []  # B cannot see A's rule
    assert detail.status_code == 404  # no existence leak
    assert deleted.status_code == 404

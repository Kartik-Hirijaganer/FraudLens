"""Repository tests (plan §16 Phase 2: "repository scoping rejects cross-agency"). Verify
that TenantScopedRepository never returns or accepts rows outside its bound agency, and that
AgencyRepository resolves the platform table by uuid/str and fails soft on bad/missing ids."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import Agency, User, UserRole
from fraudlens_backend.db.repositories import AgencyRepository, TenantScopedRepository


async def _add_agency(session: AsyncSession, name: str, slug: str) -> Agency:
    """Insert and flush a platform agency row, returning it (with its generated id)."""
    agency = Agency(name=name, slug=slug)
    session.add(agency)
    await session.flush()
    return agency


async def _add_user(session: AsyncSession, agency_id: uuid.UUID, email: str) -> User:
    """Insert and flush a tenant user under the given agency."""
    user = User(agency_id=agency_id, email=email, display_name="T", role=UserRole.ANALYST)
    session.add(user)
    await session.flush()
    return user


async def test_get_is_scoped_to_bound_agency(db_session: AsyncSession) -> None:
    agency_a = await _add_agency(db_session, "A", "a")
    agency_b = await _add_agency(db_session, "B", "b")
    user_a = await _add_user(db_session, agency_a.id, "u@a.test")

    repo_a = TenantScopedRepository(db_session, User, agency_a.id)
    repo_b = TenantScopedRepository(db_session, User, agency_b.id)

    assert (await repo_a.get(user_a.id)) is not None
    # Cross-agency read of the SAME id resolves to nothing (no tenant leak).
    assert (await repo_b.get(user_a.id)) is None
    assert repo_a.agency_id == agency_a.id


async def test_list_returns_only_bound_agency_rows(db_session: AsyncSession) -> None:
    agency_a = await _add_agency(db_session, "A", "a")
    agency_b = await _add_agency(db_session, "B", "b")
    await _add_user(db_session, agency_a.id, "one@a.test")
    await _add_user(db_session, agency_a.id, "two@a.test")
    await _add_user(db_session, agency_b.id, "one@b.test")

    rows = await TenantScopedRepository(db_session, User, agency_a.id).list()
    assert {u.email for u in rows} == {"one@a.test", "two@a.test"}
    assert all(u.agency_id == agency_a.id for u in rows)


async def test_add_stamps_bound_agency(db_session: AsyncSession) -> None:
    agency_a = await _add_agency(db_session, "A", "a")
    agency_b = await _add_agency(db_session, "B", "b")
    repo_a = TenantScopedRepository(db_session, User, agency_a.id)
    repo_b = TenantScopedRepository(db_session, User, agency_b.id)

    # The entity is constructed pointing at agency_b, but add() must stamp it to agency_a.
    stray = User(agency_id=agency_b.id, email="x@a.test", display_name="X", role=UserRole.REVIEWER)
    saved = await repo_a.add(stray)
    assert saved.agency_id == agency_a.id
    # A repo bound to agency_b must not see the row stamped into agency_a.
    assert (await repo_b.get(saved.id)) is None


async def test_agency_repo_resolves_uuid_str_and_fails_soft(db_session: AsyncSession) -> None:
    agency = await _add_agency(db_session, "Acme", "acme")
    repo = AgencyRepository(db_session)

    by_uuid = await repo.get(agency.id)
    by_str = await repo.get(str(agency.id))
    assert by_uuid is not None and by_uuid.slug == "acme"
    assert by_str is not None and by_str.slug == "acme"
    # Malformed id and a well-formed-but-absent id both resolve to None (route maps to 404).
    assert (await repo.get("not-a-uuid")) is None
    assert (await repo.get(uuid.uuid4())) is None

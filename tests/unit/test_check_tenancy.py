"""Tests for the tenancy invariant checker (plan §9.3). The live schema must pass, and the
checker must flag a tenant table missing/unindexed agency_id, a platform table that wrongly
carries agency_id, and a stale allowlist entry — honoring the platform allowlist."""

from __future__ import annotations

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, Table, Uuid

from check_tenancy import find_violations, main
from fraudlens_backend.db.models import PLATFORM_TABLES, Base


def _md_with_agencies() -> MetaData:
    """Return a fresh MetaData containing only the platform `agencies` table."""
    metadata = MetaData()
    Table("agencies", metadata, Column("id", Uuid, primary_key=True))
    return metadata


def test_live_schema_has_no_violations() -> None:
    assert find_violations(Base.metadata, PLATFORM_TABLES) == []


def test_main_exits_zero_on_live_schema(capsys: pytest.CaptureFixture[str]) -> None:
    assert main() == 0
    assert "check_tenancy OK" in capsys.readouterr().out


def test_tenant_table_missing_agency_id_is_flagged() -> None:
    metadata = _md_with_agencies()
    Table("widgets", metadata, Column("id", Uuid, primary_key=True))
    violations = find_violations(metadata, {"agencies"})
    assert any("widgets" in v and "missing agency_id" in v for v in violations)


def test_unindexed_agency_id_is_flagged() -> None:
    metadata = _md_with_agencies()
    Table(
        "widgets",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("agency_id", Uuid, ForeignKey("agencies.id")),  # FK but not indexed
    )
    violations = find_violations(metadata, {"agencies"})
    assert any("widgets" in v and "indexed" in v for v in violations)


def test_indexed_agency_id_passes() -> None:
    metadata = _md_with_agencies()
    Table(
        "widgets",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("agency_id", Uuid, ForeignKey("agencies.id"), index=True),
    )
    assert find_violations(metadata, {"agencies"}) == []


def test_platform_table_with_agency_id_is_flagged() -> None:
    metadata = _md_with_agencies()
    Table(
        "registry",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("agency_id", Uuid, ForeignKey("agencies.id"), index=True),
    )
    violations = find_violations(metadata, {"agencies", "registry"})
    assert any("registry" in v and "must NOT carry agency_id" in v for v in violations)


def test_stale_allowlist_entry_is_flagged() -> None:
    metadata = _md_with_agencies()
    violations = find_violations(metadata, {"agencies", "ghost"})
    assert any("ghost" in v and "not defined" in v for v in violations)

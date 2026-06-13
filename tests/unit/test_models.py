"""Model-level invariants (plan §9): the audit `metadata` column mapping, nullable
global-or-tenant `agency_id`, reuse of the canonical RiskBand enum, and the platform/tenant
split. These complement the runtime tenancy check with focused ORM assertions."""

from __future__ import annotations

from fraudlens_backend.db.models import (
    PLATFORM_TABLES,
    AmlRule,
    AuditLog,
    Base,
    JobExecution,
    SystemConfig,
    Transaction,
)
from fraudlens_core import RiskBand


def test_audit_log_column_is_metadata_attribute_is_meta() -> None:
    # SQLAlchemy reserves `metadata` on the declarative base, so the attribute is `meta`
    # while the persisted column keeps the plan's name `metadata`.
    columns = set(AuditLog.__table__.columns.keys())
    assert "metadata" in columns
    assert "meta" not in columns
    assert AuditLog.meta.property.columns[0].name == "metadata"


def test_global_or_tenant_tables_have_nullable_agency_id() -> None:
    for model in (AmlRule, SystemConfig, JobExecution, AuditLog):
        assert model.__table__.columns["agency_id"].nullable is True


def test_transaction_reuses_core_risk_band() -> None:
    risk_band = Transaction.__table__.columns["risk_band"].type
    assert set(risk_band.enums) == {band.value for band in RiskBand}


def test_platform_tables_carry_no_agency_id() -> None:
    for name in PLATFORM_TABLES:
        assert "agency_id" not in Base.metadata.tables[name].columns

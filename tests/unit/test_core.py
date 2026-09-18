"""Unit tests for fraudlens-core domain types and tenant-isolation helpers."""

from __future__ import annotations

import pytest

from fraudlens_core import RiskBand, TenantIsolationError, require_agency_id


def test_risk_band_is_str_enum() -> None:
    assert RiskBand.HIGH == "high"
    assert set(RiskBand) == {RiskBand.LOW, RiskBand.MEDIUM, RiskBand.HIGH, RiskBand.CRITICAL}


def test_require_agency_id_success() -> None:
    assert require_agency_id("acme", "acme") == "acme"
    assert require_agency_id("acme", None) == "acme"  # claim authoritative when none requested


def test_require_agency_id_missing_claim() -> None:
    for empty in ("", None):
        with pytest.raises(TenantIsolationError) as excinfo:
            require_agency_id(empty, "acme")
        assert excinfo.value.reason == "missing"


def test_require_agency_id_mismatch_does_not_leak_values() -> None:
    with pytest.raises(TenantIsolationError) as excinfo:
        require_agency_id("acme", "evil-corp")
    assert excinfo.value.reason == "mismatch"
    message = str(excinfo.value)
    assert "acme" not in message
    assert "evil-corp" not in message

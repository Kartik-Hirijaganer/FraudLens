"""Unit tests for fraudlens-core domain types and tenant-isolation helpers."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from fraudlens_core import (
    RiskBand,
    TenantIsolationError,
    TransactionSummary,
    require_agency_id,
)


def test_transaction_summary_valid_defaults_to_low_risk() -> None:
    summary = TransactionSummary(
        transaction_id="t1", agency_id="acme", amount=Decimal("10.50"), currency="USD"
    )
    assert summary.risk_band is RiskBand.LOW
    assert summary.currency == "USD"


def test_transaction_summary_rejects_bad_currency_length() -> None:
    with pytest.raises(ValidationError):
        TransactionSummary(
            transaction_id="t1", agency_id="acme", amount=Decimal("1"), currency="US"
        )


def test_transaction_summary_rejects_negative_amount() -> None:
    with pytest.raises(ValidationError):
        TransactionSummary(
            transaction_id="t1", agency_id="acme", amount=Decimal("-1"), currency="USD"
        )


def test_transaction_summary_is_frozen() -> None:
    summary = TransactionSummary(
        transaction_id="t1", agency_id="acme", amount=Decimal("1"), currency="USD"
    )
    with pytest.raises(ValidationError):
        summary.agency_id = "other"  # type: ignore[misc]


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

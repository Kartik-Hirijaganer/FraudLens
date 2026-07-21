"""Shared source->canonical mapping tests (real-AML training plan Phase 1: the single
`lib.aml_mapping` module both the trainer and the IEEE importer run rows through). Asserts
every Payment Format / ProductCD maps to a channel token the scorer's `channel_risk` grades,
the currency->country proxy is ISO-3166 alpha-2, direction + windowing keys behave, and
blank/None inputs fall back to the documented defaults (null handling) rather than raising."""

from __future__ import annotations

import pytest

from fraudlens_ml.scoring.features import (
    _CHANNEL_RISK,
    _DEFAULT_CHANNEL_RISK,
    _DEFAULT_COUNTRY_RISK,
    channel_risk,
    country_risk,
)
from lib.aml_mapping import (
    IEEE_EPOCH,
    ibm_account_key,
    ibm_channel,
    ibm_country,
    ibm_currency,
    ibm_is_outbound,
    ieee_channel,
    ieee_country,
)


@pytest.mark.parametrize(
    ("payment_format", "expected"),
    [
        ("ACH", "ach"),
        ("Wire", "wire"),
        ("Cash", "cash"),
        ("Credit Card", "card"),
        ("Bitcoin", "crypto"),
        ("  wire  ", "wire"),  # normalized (strip + case-insensitive)
    ],
)
def test_ibm_channel_maps_graded_formats(payment_format: str, expected: str) -> None:
    token = ibm_channel(payment_format)
    assert token == expected
    # A "real" token: the scorer explicitly grades it (anti-skew: the trainer and the scorer
    # read the same graded weight, not an unseen-value fallback).
    assert token in _CHANNEL_RISK


@pytest.mark.parametrize("payment_format", ["Cheque", "Reinvestment", "Something Else", None, ""])
def test_ibm_channel_defaults_for_ungraded_or_null(payment_format: str | None) -> None:
    token = ibm_channel(payment_format)
    # Ungraded instruments resolve to the scorer's documented default weight (never mislabeled).
    assert channel_risk(token) == _DEFAULT_CHANNEL_RISK


@pytest.mark.parametrize(
    ("currency", "expected"),
    [("US Dollar", "US"), ("Euro", "DE"), ("UK Pound", "GB"), ("brazil real", "BR")],
)
def test_ibm_country_proxies_currency(currency: str, expected: str) -> None:
    assert ibm_country(currency) == expected


@pytest.mark.parametrize("currency", ["Bitcoin", "Klingon Dollar", None, ""])
def test_ibm_country_unknown_is_iso_unknown(currency: str | None) -> None:
    token = ibm_country(currency)
    assert token == "ZZ"  # ISO user-assigned "unknown"
    assert country_risk(token) == _DEFAULT_COUNTRY_RISK


@pytest.mark.parametrize(
    ("currency", "expected"),
    [("US Dollar", "USD"), ("Euro", "EUR"), ("UK Pound", "GBP"), ("Bitcoin", "XBT")],
)
def test_ibm_currency_maps_canonical_codes(currency: str, expected: str) -> None:
    assert ibm_currency(currency) == expected


def test_ibm_currency_unknown_uses_iso_no_currency() -> None:
    assert ibm_currency(None) == "XXX"
    assert ibm_currency("Martian Credits") == "XXX"


def test_ibm_is_outbound_from_role() -> None:
    assert ibm_is_outbound(is_sender=True) == 1.0
    assert ibm_is_outbound(is_sender=False) == 0.0


def test_ibm_account_key_composes_bank_and_account() -> None:
    key = ibm_account_key(" 11 ", " 8000123 ")
    assert key == "11\x1f8000123"
    # Distinct (bank, account) pairs never collide on the join.
    assert ibm_account_key("1", "18") != ibm_account_key("11", "8")


@pytest.mark.parametrize(
    ("product_cd", "expected"), [("W", "card"), ("R", "ach"), ("H", "wire"), ("S", "cash")]
)
def test_ieee_channel_maps_product_codes(product_cd: str, expected: str) -> None:
    assert ieee_channel(product_cd) == expected


@pytest.mark.parametrize("product_cd", ["Z", None, ""])
def test_ieee_channel_defaults_for_unknown(product_cd: str | None) -> None:
    assert channel_risk(ieee_channel(product_cd)) == _DEFAULT_CHANNEL_RISK


def test_ieee_country_maps_addr2_else_us_default() -> None:
    assert ieee_country("87") == "US"
    assert ieee_country(None) == "US"  # US-centric documented default
    assert ieee_country("") == "US"
    assert ieee_country("9999") == "US"  # unrecognized addr2 -> default


def test_ieee_epoch_is_the_documented_reference_instant() -> None:
    assert IEEE_EPOCH.year == 2017
    assert IEEE_EPOCH.month == 12
    assert IEEE_EPOCH.day == 1
    assert IEEE_EPOCH.tzinfo is not None

"""Canonical transaction schema tests (plan §16 Phase 3): build_canonical normalizes +
validates every field once (shared by the API and the importer), and compute_feature_hash is
a deterministic, PHI-free fingerprint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from fraudlens_core import SchemaValidationError, build_canonical, compute_feature_hash

_NOW = datetime(2026, 6, 12, tzinfo=UTC)


def _build(**overrides: object):
    """Build a CanonicalTransaction from valid defaults with per-test overrides."""
    params: dict[str, object] = {
        "external_id": "T1",
        "amount": "100.50",
        "currency": "usd",
        "occurred_at": datetime(2026, 1, 1, tzinfo=UTC),
        "origin_account": "4111111111111111",
        "dest_account": "987654321",
        "channel": "wire",
        "country": "us",
        "now": _NOW,
    }
    params.update(overrides)
    return build_canonical(**params)  # type: ignore[arg-type]


def test_build_canonical_normalizes_codes_and_amount() -> None:
    canonical = _build()
    assert canonical.currency == "USD"  # uppercased
    assert canonical.country == "US"
    assert canonical.amount == Decimal("100.50")


def test_build_canonical_assumes_utc_for_naive_timestamp() -> None:
    canonical = _build(occurred_at=datetime(2026, 1, 1))  # naive
    assert canonical.occurred_at.tzinfo is not None


@pytest.mark.parametrize(
    ("overrides", "field", "reason"),
    [
        ({"currency": "US"}, "currency", "not_iso4217"),
        ({"country": "USA"}, "country", "not_iso3166"),
        ({"amount": "-1"}, "amount", "not_positive"),
        ({"amount": "0"}, "amount", "not_positive"),
        ({"amount": "abc"}, "amount", "not_a_number"),
        ({"amount": "NaN"}, "amount", "not_finite"),
        ({"external_id": "   "}, "external_id", "required"),
        ({"channel": "x" * 200}, "channel", "too_long"),
        ({"occurred_at": _NOW + timedelta(days=1)}, "occurred_at", "in_future"),
    ],
)
def test_build_canonical_rejects_invalid(
    overrides: dict[str, object], field: str, reason: str
) -> None:
    with pytest.raises(SchemaValidationError) as excinfo:
        _build(**overrides)
    assert excinfo.value.field == field
    assert excinfo.value.reason == reason


def test_feature_hash_is_deterministic_and_hex() -> None:
    canonical = _build()
    digest = compute_feature_hash(canonical)
    assert len(digest) == 64
    assert digest == compute_feature_hash(_build())


def test_feature_hash_changes_with_account_and_excludes_raw_value() -> None:
    base = compute_feature_hash(_build())
    other = compute_feature_hash(_build(origin_account="5555555555554444"))
    assert base != other
    # The raw account never appears in the (one-way) fingerprint.
    assert "4111111111111111" not in compute_feature_hash(_build())

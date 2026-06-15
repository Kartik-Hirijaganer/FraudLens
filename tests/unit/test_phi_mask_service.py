"""PhiMasker service tests (plan §8.2 / §17 `pytest -k phi`): the ingest masker produces
the persist-safe MaskedTransaction — masked account identifiers, scrubbed string features,
non-string features passed through, and a deterministic feature hash."""

from __future__ import annotations

from datetime import UTC, datetime

from fraudlens_backend.services.phi_mask import PhiMasker
from fraudlens_core import build_canonical


def _canonical(**overrides: object):
    """Build a CanonicalTransaction with valid defaults for masker tests."""
    params: dict[str, object] = {
        "external_id": "T1",
        "amount": "10.00",
        "currency": "USD",
        "occurred_at": datetime(2026, 1, 1, tzinfo=UTC),
        "origin_account": "4111111111111111",
        "dest_account": "987654321",
        "channel": "wire",
        "country": "US",
        "now": datetime(2026, 6, 12, tzinfo=UTC),
    }
    params.update(overrides)
    return build_canonical(**params)  # type: ignore[arg-type]


def test_mask_masks_accounts_and_sets_hash() -> None:
    masked = PhiMasker().mask(_canonical())
    assert masked.origin_account.endswith("1111")
    assert "4111111111111111" not in masked.origin_account
    assert masked.dest_account.endswith("4321")
    assert len(masked.feature_hash) == 64
    # Aggregate report counts the two masked identifiers (counts only, no values).
    assert masked.report.total >= 2


def test_mask_scrubs_string_features_and_passes_through_others() -> None:
    canonical = _canonical(
        features={"note": "call me at 415-555-1234", "amount_bucket": 3, "ratio": 0.5}
    )
    masked = PhiMasker().mask(canonical)
    assert "415-555-1234" not in masked.features["note"]
    assert "[REDACTED_PHONE]" in masked.features["note"]
    # Non-string values are preserved unchanged.
    assert masked.features["amount_bucket"] == 3
    assert masked.features["ratio"] == 0.5
    assert masked.report.categories.get("phone") == 1

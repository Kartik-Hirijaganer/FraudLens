"""Error-catalog tests (plan §16 Phase 3): every catalog entry is well-formed, AppError
references a code, and get_error_spec fails loudly on an unknown code."""

from __future__ import annotations

import pytest

from fraudlens_backend.models.errors import ERROR_CATALOG, AppError, get_error_spec


def test_catalog_entries_are_well_formed() -> None:
    assert ERROR_CATALOG  # non-empty
    for code, spec in ERROR_CATALOG.items():
        assert spec.code == code  # keyed by its own code
        assert 400 <= spec.http_status <= 599
        assert spec.message and not spec.message.endswith(" ")


def test_known_codes_map_to_expected_status() -> None:
    assert get_error_spec("duplicate_external_id").http_status == 409
    assert get_error_spec("payload_too_large").http_status == 413
    assert get_error_spec("unsupported_content_type").http_status == 415


def test_app_error_carries_code_and_details() -> None:
    err = AppError("duplicate_external_id", details=[{"field": "externalId", "message": "dup"}])
    assert err.code == "duplicate_external_id"
    assert err.details == [{"field": "externalId", "message": "dup"}]
    assert AppError("transaction_not_found").details is None


def test_get_error_spec_raises_on_unknown_code() -> None:
    with pytest.raises(KeyError):
        get_error_spec("nope_not_a_code")

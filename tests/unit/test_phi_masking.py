"""PHI masking tests (plan §8.2 / §17 `pytest -k phi`): deterministic masking of account
identifiers and free text must redact cards/accounts/SSN/email/phone/IBAN and report
counts only — never the original values."""

from __future__ import annotations

from fraudlens_core.phi import (
    BANK_ACCOUNT,
    CREDIT_CARD,
    EMAIL,
    IBAN,
    PHONE,
    SSN,
    mask_identifier,
    mask_text,
)

# Synthetic, well-known test identifiers (no real PHI).
_VISA = "4111111111111111"  # Luhn-valid 16-digit card
_VISA_BAD = "4111111111111112"  # same shape, fails the Luhn checksum
_IBAN = "DE89370400440532013000"  # checksum-valid IBAN
_IBAN_BAD = "DE00370400440532013000"  # IBAN shape, bad check digits


def test_mask_identifier_keeps_last_four_of_card() -> None:
    masked = mask_identifier(_VISA)
    assert masked.value.endswith("1111")
    assert masked.value[:-4] == "*" * (len(_VISA) - 4)
    assert masked.report.categories == {CREDIT_CARD: 1}
    assert _VISA not in masked.value


def test_mask_identifier_detects_iban_and_account() -> None:
    assert mask_identifier(_IBAN).report.categories == {IBAN: 1}
    # A short numeric run is neither a valid card nor IBAN -> generic bank account.
    assert mask_identifier("12345").report.categories == {BANK_ACCOUNT: 1}


def test_mask_identifier_handles_separators_and_empty() -> None:
    # Spaces/dashes are tolerated when classifying the card.
    assert mask_identifier("4111 1111 1111 1111").report.categories == {CREDIT_CARD: 1}
    empty = mask_identifier("   ")
    assert empty.value == ""
    assert empty.report.total == 0


def test_mask_identifier_short_value_is_fully_masked() -> None:
    # Fewer than four characters: nothing recognizable is left visible.
    masked = mask_identifier("12")
    assert masked.value == "****12"


def test_mask_text_redacts_every_category() -> None:
    text = (
        f"email a@b.com phone 415-555-1234 ssn 123-45-6789 "
        f"card {_VISA} iban {_IBAN} account 998877665544"
    )
    masked = mask_text(text)
    assert masked.report.categories == {
        BANK_ACCOUNT: 1,
        CREDIT_CARD: 1,
        EMAIL: 1,
        IBAN: 1,
        PHONE: 1,
        SSN: 1,
    }
    assert masked.report.total == 6
    for token in ("a@b.com", "415-555-1234", "123-45-6789", _VISA, _IBAN, "998877665544"):
        assert token not in masked.value


def test_mask_text_leaves_checksum_invalid_candidates_intact() -> None:
    # A card-shaped / IBAN-shaped span that fails its checksum is NOT masked as that kind.
    masked = mask_text(f"maybe {_VISA_BAD} or {_IBAN_BAD}")
    assert CREDIT_CARD not in masked.report.categories
    assert IBAN not in masked.report.categories
    # Both long digit runs still get caught by the generic account rule (defense-in-depth).
    assert masked.report.categories.get(BANK_ACCOUNT) == 2
    assert _VISA_BAD not in masked.value


def test_mask_text_no_phi_is_unchanged() -> None:
    masked = mask_text("a routine note with no identifiers")
    assert masked.value == "a routine note with no identifiers"
    assert masked.report.total == 0

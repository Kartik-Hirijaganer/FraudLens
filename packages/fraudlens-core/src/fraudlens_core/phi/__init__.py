"""fraudlens-core PHI masking (plan §8.2): deterministic, in-process masking of account
identifiers and free text. Re-exports are intentional (the masking helpers + result types).
"""

from fraudlens_core.phi.masking import (
    BANK_ACCOUNT,
    CREDIT_CARD,
    EMAIL,
    IBAN,
    PHONE,
    SSN,
    MaskedValue,
    MaskingReport,
    mask_identifier,
    mask_text,
)

__all__ = [
    "BANK_ACCOUNT",
    "CREDIT_CARD",
    "EMAIL",
    "IBAN",
    "PHONE",
    "SSN",
    "MaskedValue",
    "MaskingReport",
    "mask_identifier",
    "mask_text",
]

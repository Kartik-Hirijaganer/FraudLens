"""Summary: Deterministic, zero-dependency PHI/PII masking primitives (plan §8.2,
ADR-006). Pure Python — regex plus `python-stdnum` validators (Luhn for cards, IBAN
checksum for bank identifiers) — so masking runs in-process with no NLP model, no Azure
service, and no network, fitting the cold-start budget. `mask_identifier` masks a single
structured account field (origin/dest account) down to its last four characters, tagging
the detected kind; `mask_text` scans free text (CSV feature values, the client-error sink
message) and replaces card/IBAN/SSN/email/phone/account spans with category tokens. Both
return a counts-only `MaskingReport` (how many of each category) and never the original
value, so callers can audit "what was masked" without ever logging PHI.

Key classes:
- MaskingReport: counts-only summary of a masking pass (category -> count, total).
- MaskedValue: a masked string plus its MaskingReport.

Key functions:
- mask_identifier: mask one account identifier, preserving only the last four chars.
- mask_text: redact PHI-shaped spans in free text, returning the masked text + report.

Notes:
- The card check uses `stdnum.luhn` and the IBAN check `stdnum.iban`, so a long digit run
  is only treated as a card/IBAN when its checksum actually validates (fewer false hits).
- mask_text applies categories in a fixed order (email, ssn, card, iban, phone, account)
  so a card is consumed before the generic account rule can re-match its digits.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field
from stdnum import iban, luhn

# Category labels (stable; they appear in counts-only reports and audit metadata).
CREDIT_CARD = "credit_card"
IBAN = "iban"
BANK_ACCOUNT = "bank_account"
SSN = "ssn"
EMAIL = "email"
PHONE = "phone"

_KEEP_LAST = 4
_MASK_CHAR = "*"
_MIN_CARD_DIGITS = 13
_MAX_CARD_DIGITS = 19
_MIN_ACCOUNT_DIGITS = 8

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")
_CARD_RE = re.compile(r"(?<![\w-])(?:\d[ -]?){13,19}(?![\w-])")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_ACCOUNT_RE = re.compile(r"(?<!\d)\d{8,}(?!\d)")


class MaskingReport(BaseModel):
    """Counts-only record of a masking pass — never the values that were masked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    categories: dict[str, int] = Field(
        default_factory=dict, description="Masked-span count per category (sorted keys)."
    )
    total: int = Field(default=0, ge=0, description="Total number of spans masked.")


class MaskedValue(BaseModel):
    """A masked string plus the counts-only report describing what was redacted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str = Field(..., description="The masked text — safe to store/log.")
    report: MaskingReport = Field(..., description="Counts-only masking report.")


def _report(counts: Counter[str]) -> MaskingReport:
    """Build a MaskingReport from a category counter (sorted, with a total)."""
    sorted_counts = dict(sorted(counts.items()))
    return MaskingReport(categories=sorted_counts, total=sum(sorted_counts.values()))


def _keep_last_four(raw: str) -> str:
    """Return the value with every character but the last four replaced by a mask char."""
    visible = raw[-_KEEP_LAST:]
    hidden = max(len(raw) - len(visible), _KEEP_LAST)
    return f"{_MASK_CHAR * hidden}{visible}"


def _classify_identifier(raw: str) -> str:
    """Return the detected category for a structured account identifier."""
    compact = raw.replace(" ", "").replace("-", "")
    if iban.is_valid(compact):
        return IBAN
    digits = re.sub(r"\D", "", raw)
    if _MIN_CARD_DIGITS <= len(digits) <= _MAX_CARD_DIGITS and luhn.is_valid(digits):
        return CREDIT_CARD
    return BANK_ACCOUNT


def mask_identifier(raw: str) -> MaskedValue:
    """Mask one account identifier to its last four characters, tagging the kind."""
    cleaned = raw.strip()
    if not cleaned:
        return MaskedValue(value="", report=_report(Counter()))
    category = _classify_identifier(cleaned)
    return MaskedValue(value=_keep_last_four(cleaned), report=_report(Counter({category: 1})))


def _mask_spans(text: str, pattern: re.Pattern[str], category: str, counts: Counter[str]) -> str:
    """Replace every span matching pattern with a category token, counting each hit."""

    def replace(_match: re.Match[str]) -> str:
        counts[category] += 1
        return f"[REDACTED_{category.upper()}]"

    return pattern.sub(replace, text)


def _mask_validated(
    text: str,
    pattern: re.Pattern[str],
    category: str,
    counts: Counter[str],
    is_valid: Callable[[str], bool],
) -> str:
    """Replace only spans whose checksum validates (cards/IBANs); leave others intact."""

    def replace(match: re.Match[str]) -> str:
        candidate = re.sub(r"[ -]", "", match.group(0))
        if is_valid(candidate):
            counts[category] += 1
            return f"[REDACTED_{category.upper()}]"
        return match.group(0)

    return pattern.sub(replace, text)


def mask_text(raw: str) -> MaskedValue:
    """Redact PHI-shaped spans in free text, returning the masked text + a counts report."""
    counts: Counter[str] = Counter()
    masked = _mask_spans(raw, _EMAIL_RE, EMAIL, counts)
    masked = _mask_spans(masked, _SSN_RE, SSN, counts)
    masked = _mask_validated(masked, _CARD_RE, CREDIT_CARD, counts, luhn.is_valid)
    masked = _mask_validated(masked, _IBAN_RE, IBAN, counts, iban.is_valid)
    masked = _mask_spans(masked, _PHONE_RE, PHONE, counts)
    masked = _mask_spans(masked, _ACCOUNT_RE, BANK_ACCOUNT, counts)
    return MaskedValue(value=masked, report=_report(counts))

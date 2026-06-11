"""Summary: Deterministic local PHI-like masking for LLM inputs. It masks common
synthetic identifiers before content reaches any provider and reports counts only.

Key classes:
- MaskedText: Masked text plus counts-only report.

Key functions:
- mask_text: Mask PHI-like spans in one text string.
- mask_texts: Mask a sequence of text strings and aggregate reports.

Notes:
- Regex masking is defense-in-depth and intentionally fail-closed at the client layer.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_llm.models import MaskingReport, PhiMaskingMode

_MIN_CARD_DIGITS = 13
_MAX_CARD_DIGITS = 19
_LUHN_BASE = 10
_LUHN_DOUBLE_THRESHOLD = 9

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("us_ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("dob", re.compile(r"\b(?:DOB|date of birth)[:\s]+(?:\d{1,2}[/-]){2}\d{2,4}\b", re.I)),
    ("mrn_member_id", re.compile(r"\b(?:MRN|member id|member_id)[:#\s-]*[A-Z0-9]{5,}\b", re.I)),
    (
        "street_address",
        re.compile(
            r"\b\d{1,6}\s+[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,4}\s+"
            r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Lane|Ln|Drive|Dr)\b",
            re.I,
        ),
    ),
    ("phone", re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b")),
)
_CARD_CANDIDATE_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


class MaskedText(BaseModel):
    """Masked text and counts-only report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(..., description="Masked text.")
    report: MaskingReport = Field(..., description="Counts-only masking report.")


def mask_text(text: str, mode: PhiMaskingMode) -> MaskedText:
    """Mask PHI-like spans in one text string."""
    if mode == PhiMaskingMode.OFF:
        return MaskedText(
            text=text,
            report=MaskingReport(mode=mode, counts={}, total_masked=0),
        )

    counts: Counter[str] = Counter()
    masked = _mask_credit_cards(text, counts)
    for category, pattern in _PATTERNS:
        masked = _mask_pattern(masked, pattern=pattern, category=category, counts=counts)
    report_counts = dict(sorted(counts.items()))
    return MaskedText(
        text=masked,
        report=MaskingReport(
            mode=mode,
            counts=report_counts,
            total_masked=sum(report_counts.values()),
        ),
    )


def mask_texts(texts: Sequence[str], mode: PhiMaskingMode) -> tuple[list[str], MaskingReport]:
    """Mask a sequence of strings and aggregate their reports."""
    masked_texts: list[str] = []
    aggregate: Counter[str] = Counter()
    for text in texts:
        masked = mask_text(text, mode)
        masked_texts.append(masked.text)
        aggregate.update(masked.report.counts)
    report_counts = dict(sorted(aggregate.items()))
    return masked_texts, MaskingReport(
        mode=mode,
        counts=report_counts,
        total_masked=sum(report_counts.values()),
    )


def _mask_credit_cards(text: str, counts: Counter[str]) -> str:
    """Mask Luhn-valid credit-card-like candidates."""

    def replace(match: re.Match[str]) -> str:
        candidate = match.group(0)
        digits = re.sub(r"\D", "", candidate)
        if _MIN_CARD_DIGITS <= len(digits) <= _MAX_CARD_DIGITS and _passes_luhn(digits):
            return _replacement("credit_card", counts)
        return candidate

    return _CARD_CANDIDATE_RE.sub(replace, text)


def _mask_pattern(
    text: str,
    *,
    pattern: re.Pattern[str],
    category: str,
    counts: Counter[str],
) -> str:
    """Mask all matches for a category."""

    def replace(_match: re.Match[str]) -> str:
        return _replacement(category, counts)

    return pattern.sub(replace, text)


def _replacement(category: str, counts: Counter[str]) -> str:
    """Return a replacement token and increment the category count."""
    counts[category] += 1
    return f"[REDACTED_{category.upper()}]"


def _passes_luhn(digits: str) -> bool:
    """Return whether digits pass the Luhn checksum."""
    total = 0
    reverse_digits = digits[::-1]
    for index, character in enumerate(reverse_digits):
        value = int(character)
        if index % 2 == 1:
            value *= 2
            if value > _LUHN_DOUBLE_THRESHOLD:
                value -= _LUHN_DOUBLE_THRESHOLD
        total += value
    return total % _LUHN_BASE == 0

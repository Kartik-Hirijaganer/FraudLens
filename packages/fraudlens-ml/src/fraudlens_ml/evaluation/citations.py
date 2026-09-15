"""Summary: Deterministic SAR citation, unsupported-claim, false-positive, and required-fact
quality metrics. The functions accept closed sets or boolean outcomes, return frozen Pydantic
results with explicit counts, and make empty-denominator behavior conservative and reproducible.

Key classes:
- CitationMetrics: precision/recall and supporting citation counts.
- BinaryDetectionMetrics: recall or false-positive rate with confusion counts.
- CoverageMetrics: required-fact coverage and matched/missing facts.

Key functions:
- citation_precision_recall: compare produced citation ids with offered and expected ids.
- unsupported_claim_recall: measure planted unsupported claims detected by the gate.
- false_positive_rate: measure clean claims incorrectly flagged as unsupported.
- required_fact_coverage: measure normalized required facts present in rendered output.

Notes:
- Citation precision treats an empty produced set as perfect only when nothing was expected;
  citation recall treats an empty expected set as complete.
- Required facts use case-insensitive whitespace-normalized substring matching so formatting alone
  cannot change a deterministic benchmark result.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence

from pydantic import BaseModel, ConfigDict, Field

_WHITESPACE = re.compile(r"\s+")


class CitationMetrics(BaseModel):
    """Precision/recall for produced citations against offered and expected closed sets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    precision: float = Field(..., ge=0, le=1, description="Produced ids present in offered ids.")
    recall: float = Field(..., ge=0, le=1, description="Expected ids present in produced ids.")
    produced_count: int = Field(..., ge=0, description="Unique produced citation ids.")
    valid_count: int = Field(..., ge=0, description="Produced ids present in offered evidence.")
    expected_count: int = Field(..., ge=0, description="Unique expected citation ids.")
    recalled_count: int = Field(..., ge=0, description="Expected ids present in output.")
    fabricated_ids: tuple[str, ...] = Field(
        default=(), description="Produced ids absent from offered evidence, in stable order."
    )
    missed_ids: tuple[str, ...] = Field(
        default=(), description="Expected ids absent from produced output, in stable order."
    )


class BinaryDetectionMetrics(BaseModel):
    """Confusion counts and one named rate for a deterministic binary detector."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rate: float = Field(..., ge=0, le=1, description="Recall or false-positive rate requested.")
    positives: int = Field(..., ge=0, description="Actual positive examples.")
    negatives: int = Field(..., ge=0, description="Actual negative examples.")
    true_positives: int = Field(..., ge=0, description="Positive examples correctly detected.")
    false_positives: int = Field(..., ge=0, description="Negative examples incorrectly detected.")


class CoverageMetrics(BaseModel):
    """Coverage of required case facts in one rendered draft."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    coverage: float = Field(..., ge=0, le=1, description="Matched required facts / all required.")
    required_count: int = Field(..., ge=0, description="Unique required facts.")
    matched: tuple[str, ...] = Field(default=(), description="Required facts found in the draft.")
    missing: tuple[str, ...] = Field(
        default=(), description="Required facts absent from the draft."
    )


def _ordered_unique(values: Collection[str]) -> tuple[str, ...]:
    """Return non-empty values once, preserving input order where one exists."""
    return tuple(dict.fromkeys(value for value in values if value))


def citation_precision_recall(
    produced: Collection[str],
    offered: Collection[str],
    expected: Collection[str],
) -> CitationMetrics:
    """Measure produced citation validity and expected-citation recall."""
    produced_ids = _ordered_unique(produced)
    offered_ids = set(offered)
    expected_ids = _ordered_unique(expected)
    valid = tuple(item for item in produced_ids if item in offered_ids)
    fabricated = tuple(item for item in produced_ids if item not in offered_ids)
    recalled = tuple(item for item in expected_ids if item in set(produced_ids))
    missed = tuple(item for item in expected_ids if item not in set(produced_ids))
    precision = len(valid) / len(produced_ids) if produced_ids else float(not expected_ids)
    recall = len(recalled) / len(expected_ids) if expected_ids else 1.0
    return CitationMetrics(
        precision=precision,
        recall=recall,
        produced_count=len(produced_ids),
        valid_count=len(valid),
        expected_count=len(expected_ids),
        recalled_count=len(recalled),
        fabricated_ids=fabricated,
        missed_ids=missed,
    )


def unsupported_claim_recall(
    planted_claim_ids: Collection[str], detected_claim_ids: Collection[str]
) -> BinaryDetectionMetrics:
    """Return recall for planted unsupported claims detected by stable claim id."""
    planted = set(planted_claim_ids)
    detected = set(detected_claim_ids)
    true_positives = len(planted & detected)
    return BinaryDetectionMetrics(
        rate=true_positives / len(planted) if planted else 1.0,
        positives=len(planted),
        negatives=0,
        true_positives=true_positives,
        false_positives=len(detected - planted),
    )


def false_positive_rate(
    actual_supported: Sequence[bool], flagged: Sequence[bool]
) -> BinaryDetectionMetrics:
    """Return the detector false-positive rate over aligned supported/flagged decisions."""
    if len(actual_supported) != len(flagged):
        raise ValueError("supported and flagged decisions must have equal length")
    negatives = sum(1 for supported in actual_supported if supported)
    positives = len(actual_supported) - negatives
    pairs = tuple(zip(actual_supported, flagged, strict=True))
    false_positives = sum(1 for supported, was_flagged in pairs if supported and was_flagged)
    true_positives = sum(1 for supported, was_flagged in pairs if not supported and was_flagged)
    return BinaryDetectionMetrics(
        rate=false_positives / negatives if negatives else 0.0,
        positives=positives,
        negatives=negatives,
        true_positives=true_positives,
        false_positives=false_positives,
    )


def required_fact_coverage(required_facts: Collection[str], rendered_text: str) -> CoverageMetrics:
    """Measure case-insensitive, whitespace-normalized required-fact presence."""
    required = _ordered_unique(required_facts)
    normalized_text = _WHITESPACE.sub(" ", rendered_text).casefold()
    matched = tuple(
        fact for fact in required if _WHITESPACE.sub(" ", fact).casefold() in normalized_text
    )
    missing = tuple(fact for fact in required if fact not in set(matched))
    return CoverageMetrics(
        coverage=len(matched) / len(required) if required else 1.0,
        required_count=len(required),
        matched=matched,
        missing=missing,
    )

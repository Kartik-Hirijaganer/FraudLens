"""Summary: The PHI-free value types of the production SAR quality gate (release 0.5.0 Phase 2).
The gate itself is a backend evaluator — it needs the egress projection and the prompt/policy
configuration — but its RESULT travels on `SarDraftResult`, which lives in `fraudlens-ml`. These
types therefore live here so the ml layer never imports `fraudlens-backend` or `fraudlens-llm`
(the ruff-enforced layering) while still carrying a full, auditable verdict. `SarEvidenceFact` is
one entry of the deterministic evidence catalog derived from the already-projected model input;
`SarClaimFact` is the machine-readable fact a draft asserts against one catalog ref, so a claim
that carries a VALID citation while stating an altered amount is detectable by typed equality
rather than by prose inspection. `canonical_fact_value` is the single, total normalisation used on
both sides of that comparison — money to a scale-free `Decimal` string, instants to a UTC ISO
string, enums to a case-folded token — and it returns None rather than guessing, so an
un-normalisable assertion fails the gate instead of silently passing.
`DeterministicReviewChecks` lives here (rather than in the backend agent package that still owns
`evaluate_draft_checks`) for the same layering reason: it is embedded verbatim in the gate result.

Key classes:
- SarGateReason: the stable, PHI-free reason codes a gate rejection may carry.
- SarFactKind: the canonical type of one evidence fact (decides its normalisation).
- SarEvidenceFact: one catalog fact a draft may reference and must match.
- SarClaimFact: one machine-readable fact a claim asserts against a catalog ref.
- SarFactMismatch: one asserted fact that does not equal its catalog value.
- DeterministicReviewChecks: immutable claim-evidence and citation-membership findings.
- SarQualityGateResult: the full deterministic verdict recorded on a draft and its attempts.

Key functions:
- canonical_fact_value: normalise one raw value by kind, or None when it cannot be normalised.

Notes:
- Every field is PHI-free by construction: refs are synthetic catalog ids, values are already
  projected non-PHI facts, and reason codes are a closed vocabulary.
- Normalisation is explicit and TOTAL: every kind has exactly one rule and no fallback coercion,
  so the same input always produces the same verdict (the gate is replayable evidence). Numbers
  are rendered in positional form, never exponent form, so '9500' never becomes '9.5E+3'.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from fraudlens_ml.evaluation.citations import CitationMetrics

_GATE_MODEL_CONFIG = ConfigDict(
    alias_generator=to_camel, populate_by_name=True, extra="forbid", frozen=True
)


class SarGateReason(StrEnum):
    """The closed set of deterministic reasons a SAR draft may be rejected for."""

    SCHEMA_INVALID = "schema_invalid"
    NO_CITATIONS = "no_citations"
    CITATION_FABRICATED = "citation_fabricated"
    CITATION_DUPLICATED = "citation_duplicated"
    CLAIM_MISSING_EVIDENCE = "claim_missing_evidence"
    EVIDENCE_REF_UNRESOLVED = "evidence_ref_unresolved"
    EVIDENCE_EMPTY = "evidence_empty"
    ASSERTED_FACT_MISMATCH = "asserted_fact_mismatch"
    UNMAPPED_NARRATIVE_FACT = "unmapped_narrative_fact"
    FINCEN_ELEMENT_MISSING = "fincen_element_missing"
    OUTPUT_TRUNCATED = "output_truncated"


class SarFactKind(StrEnum):
    """The canonical type of an evidence fact, which decides how it is normalised."""

    MONEY = "money"
    INSTANT = "instant"
    ENUM = "enum"
    NUMBER = "number"


def canonical_fact_value(kind: SarFactKind, raw: str) -> str | None:
    """Normalise one raw value by its canonical kind; None when it cannot be normalised."""
    candidate = raw.strip()
    if not candidate:
        return None
    if kind is SarFactKind.ENUM:
        return candidate.casefold()
    if kind is SarFactKind.INSTANT:
        return _canonical_instant(candidate)
    return _canonical_number(candidate)


def _canonical_instant(candidate: str) -> str | None:
    """Render an ISO-8601 instant as a UTC ISO string, treating naive input as UTC."""
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    resolved = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return resolved.astimezone(UTC).isoformat()


def _canonical_number(candidate: str) -> str | None:
    """Render a decimal number scale-free and never in exponent form ('1200.00' == '1200')."""
    try:
        value = Decimal(candidate.replace(",", "").replace("$", ""))
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return f"{value.normalize():f}"


class SarEvidenceFact(BaseModel):
    """One deterministic catalog fact a draft may reference and must match exactly."""

    model_config = _GATE_MODEL_CONFIG

    ref: str = Field(..., min_length=1, description="Stable synthetic evidence reference id.")
    kind: SarFactKind = Field(..., description="Canonical type deciding the normalisation rule.")
    value: str = Field(..., min_length=1, description="Canonical normalised value of the fact.")
    display: str = Field(
        ..., min_length=1, description="Rendered form the narrative may legitimately contain."
    )


class SarClaimFact(BaseModel):
    """One machine-readable fact a narrative claim asserts against a catalog reference."""

    model_config = _GATE_MODEL_CONFIG

    ref: str = Field(..., min_length=1, description="Evidence reference the claim asserts about.")
    value: str = Field(..., min_length=1, description="Value the draft asserts for that fact.")


class SarFactMismatch(BaseModel):
    """One asserted fact that does not equal its catalog value (the hallucination finding)."""

    model_config = _GATE_MODEL_CONFIG

    claim_index: int = Field(..., ge=0, description="Zero-based index of the asserting claim.")
    ref: str = Field(..., min_length=1, description="Evidence reference that failed comparison.")
    expected: str = Field(
        default="", description="Canonical catalog value, empty when the ref is unknown."
    )
    asserted: str = Field(default="", description="Canonical asserted value, empty when unusable.")


class DeterministicReviewChecks(BaseModel):
    """Immutable deterministic findings used by review routing and the reviewer prompt."""

    model_config = _GATE_MODEL_CONFIG

    passed: bool = Field(..., description="Whether every deterministic grounding gate passed.")
    every_claim_has_evidence: bool = Field(
        ..., description="Whether every narrative claim carries an evidence reference."
    )
    cited_ids_are_available: bool = Field(
        ..., description="Whether every draft citation id exists in the supplied corpus evidence."
    )
    evidence_refs_are_available: bool = Field(
        ...,
        description="Whether every claim evidence reference resolves to trusted persisted data.",
    )
    unsupported_claim_indexes: tuple[int, ...] = Field(
        default=(),
        description="Zero-based indexes of claims without resolvable evidence references.",
    )
    unresolved_evidence_refs: tuple[str, ...] = Field(
        default=(),
        description="Ordered claim evidence references absent from trusted persisted data.",
    )
    fabricated_citation_ids: tuple[str, ...] = Field(
        default=(), description="Ordered draft citation ids absent from supplied corpus evidence."
    )


class SarQualityGateResult(BaseModel):
    """The deterministic verdict for one generated candidate, recorded for audit and routing."""

    model_config = _GATE_MODEL_CONFIG

    passed: bool = Field(..., description="Whether every enabled deterministic rule passed.")
    policy_version: str = Field(..., min_length=1, description="Runtime gate policy version id.")
    policy_hash: str = Field(
        ..., min_length=1, description="SHA-256 of the exact evaluated policy document."
    )
    reasons: tuple[SarGateReason, ...] = Field(
        default=(), description="Ordered stable rejection reason codes (empty when passed)."
    )
    checks: DeterministicReviewChecks = Field(
        ..., description="Claim-evidence and citation-membership findings, reused verbatim."
    )
    citation_metrics: CitationMetrics = Field(
        ..., description="Produced-versus-offered citation precision and counts."
    )
    fabricated_citation_ids: tuple[str, ...] = Field(
        default=(), description="Citation ids the draft produced that were never offered."
    )
    duplicate_citation_ids: tuple[str, ...] = Field(
        default=(), description="Citation ids the draft repeated within one list."
    )
    unsupported_claim_indexes: tuple[int, ...] = Field(
        default=(), description="Zero-based indexes of claims without resolvable evidence."
    )
    mismatched_facts: tuple[SarFactMismatch, ...] = Field(
        default=(), description="Asserted facts that do not equal their catalog values."
    )
    unmapped_narrative_spans: tuple[int, ...] = Field(
        default=(),
        description="Zero-based indexes of narrative objective values with no asserted fact.",
    )
    missing_fincen_elements: tuple[str, ...] = Field(
        default=(), description="Required FinCEN narrative elements the draft never covered."
    )
    fallback_required: bool = Field(
        default=False, description="Whether this verdict requires escalation to the next tier."
    )

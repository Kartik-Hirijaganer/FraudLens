"""Summary: The production SAR quality gate (release 0.5.0 Phase 2.3) — the deterministic
verdict every generated candidate must pass BEFORE grounding and before persistence. It is pure:
no IO, no provider call, never an LLM. It composes the existing `evaluate_draft_checks`
(claim evidence + citation membership) and `citation_precision_recall` rather than reimplementing
them, and adds the rules that did not exist before this release: citation duplication, typed
asserted-fact equality against the `SarEvidenceCatalog` (a claim carrying a VALID citation while
stating an altered amount now fails), narrative mapping of objective values onto asserted facts,
FinCEN who/what/when/where/why/how coverage, and truncation detection. Evaluating the UNGROUNDED
content is the point: `ground_citations` silently deletes fabricated ids, so after grounding the
evidence of fabrication is already destroyed.
`citation_recall` and `required_fact_coverage` are deliberately NOT runtime rules — they need
benchmark ground truth (`expectedCitationIds`, `requiredFacts`) that does not exist at request
time, so they stay benchmark-only aggregate metrics and the gate stays runnable in production.

Key classes:
- SarFincenElement: the closed FinCEN narrative-element vocabulary a draft must cover.
- SarRuntimeGatePolicy: the non-secret runtime rule switches from config/quality.yaml.
- SarQualityPolicyError:
- SarQualityGate: the deterministic evaluator producing one `SarQualityGateResult`.

Key functions:
- evaluate_sar_quality: judge one raw completion with the gate, unparseable output included.
- load_sar_gate_policy: load + validate one named `sar_quality` gate policy from the policy file.

Notes:
- Narrative mapping is governed by `require_asserted_fact_match`: both rules decide whether the
prose is traceable to declared facts, so they are enabled and disabled together.
- A TERMINAL reason is one no later tier could clear, so the result clears `fallback_required`
and the cascade stops instead of re-spending. `EVIDENCE_EMPTY` is always terminal: a case with no
trusted evidence cannot succeed anywhere. `NO_CITATIONS` is terminal only when NOTHING WAS OFFERED
to cite — retrieval is a soft enhancer that degrades to empty, and a case it returned nothing for
cannot be rescued by another model: under constrained decoding the schema forbids a citation
outright (`maxItems: 0`), and unconstrained a tier can only invent one and fail
`CITATION_FABRICATED` instead. Escalating there buys a different reason code, not a draft. When
citations WERE offered and the draft cited none, that is a model failure another tier may fix, so
it stays escalatable — the distinction is the whole point.
- `evaluate_sar_quality` is the ONE entry point for judging raw provider text: the drafter, the
benchmark, the replay pilot, and the offline cascade-quality suite all route through it, so a
draft that fails in production fails identically in evidence (AD-4.1).
- The file holds more than one named policy: `runtime_gate` is what production serves under, and
`replay_gate` is the citation-membership subset the persisted prompt-v1 benchmark output can be
judged under (that prompt never asked for claims or asserted facts). Both load through this one
loader, so there is exactly one policy schema and one policy file.
- Duplication is judged WITHIN each citation list. A claim legitimately re-states a regulation the
draft also lists at the top level, so a global de-duplication would reject well-formed drafts.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fraudlens_backend.agents.checks import evaluate_draft_checks
from fraudlens_backend.sar.evidence import SarEvidenceCatalog
from fraudlens_backend.sar.schema import SarSchemaError, parse_only
from fraudlens_backend.settings import find_config_dir
from fraudlens_ml.evaluation import CitationMetrics, citation_precision_recall
from fraudlens_ml.sar import (
    DeterministicReviewChecks,
    SarCitation,
    SarDraftContent,
    SarFactKind,
    SarFactMismatch,
    SarGateReason,
    SarQualityGateResult,
    canonical_fact_value,
)

_TRUNCATED_FINISH_REASON = "length"
_ALWAYS_TERMINAL = frozenset({SarGateReason.EVIDENCE_EMPTY})
_UNCITABLE_TERMINAL = _ALWAYS_TERMINAL | {SarGateReason.NO_CITATIONS}
_WORD_RE = re.compile(r"[a-z]+")
# An "objective value" is a money/decimal/date-shaped span, never a small ordinal: matching
# "3" in "3 rules fired" would make the narrative rule unusable. Date-time matching is explicit
# rather than a character class so a sentence-ending period is not swallowed into the instant.
_OBJECTIVE_VALUE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
    r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?"
    r"|\d+\.\d+"
    r"|\b\d{4,}\b"
)
_EMPTY_CHECKS = DeterministicReviewChecks(
    passed=False,
    every_claim_has_evidence=False,
    cited_ids_are_available=False,
    evidence_refs_are_available=False,
)
_EMPTY_METRICS = CitationMetrics(
    precision=0.0, recall=0.0, produced_count=0, valid_count=0, expected_count=0, recalled_count=0
)


RUNTIME_GATE_POLICY = "runtime_gate"
REPLAY_GATE_POLICY = "replay_gate"


class SarFincenElement(StrEnum):
    """The FinCEN narrative elements a SAR draft must cover or mark explicitly unavailable."""

    WHO = "who"
    WHAT = "what"
    WHEN = "when"
    WHERE = "where"
    WHY = "why"
    HOW = "how"


class SarRuntimeGatePolicy(BaseModel):
    """The non-secret runtime rule switches governing the production SAR quality gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_version: str = Field(..., min_length=1, description="Auditable gate policy version id.")
    require_citation: bool = Field(..., description="Reject a draft that cites no regulation.")
    allow_duplicate_citations: bool = Field(
        ..., description="Permit the same citation id more than once in one list."
    )
    require_claim_evidence: bool = Field(
        ..., description="Require resolvable evidence references on every narrative claim."
    )
    require_asserted_fact_match: bool = Field(
        ..., description="Require asserted facts and narrative values to match the catalog."
    )
    require_fincen_elements: bool = Field(
        ..., description="Require every FinCEN narrative element to be covered."
    )
    fail_on_truncation: bool = Field(
        ..., description="Reject output whose provider finish reason is a length stop."
    )
    max_tiers: int = Field(..., gt=0, description="Maximum cascade stages a profile may declare.")

    @property
    def policy_hash(self) -> str:
        """Return the SHA-256 of this exact policy document (recorded on every verdict)."""
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()


class SarQualityPolicyError(RuntimeError):
    """Raised when the runtime SAR gate policy cannot be loaded or validated."""


def load_sar_gate_policy(
    path: Path | None = None, *, policy: str = RUNTIME_GATE_POLICY
) -> SarRuntimeGatePolicy:
    """Load + validate one named `sar_quality` gate policy from the committed quality file."""
    config_path = path or (find_config_dir() / "quality.yaml")
    try:
        raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return SarRuntimeGatePolicy.model_validate(raw["sar_quality"][policy])
    except (KeyError, OSError, TypeError, yaml.YAMLError, ValidationError) as exc:
        raise SarQualityPolicyError(
            f"SAR '{policy}' gate policy is invalid: {config_path}"
        ) from exc


class SarQualityGate:
    """Deterministically decide whether one generated SAR candidate may be served."""

    def __init__(self, policy: SarRuntimeGatePolicy) -> None:
        """Bind the runtime rule switches and pre-compute their auditable hash."""
        self._policy = policy
        self._policy_hash = policy.policy_hash

    @property
    def policy(self) -> SarRuntimeGatePolicy:
        """Return the bound runtime policy."""
        return self._policy

    @property
    def policy_hash(self) -> str:
        """Return the SHA-256 of the bound policy document."""
        return self._policy_hash

    def rejected(
        self, reason: SarGateReason, *, available: tuple[SarCitation, ...] = ()
    ) -> SarQualityGateResult:
        """Build a verdict for a candidate that never produced evaluable content."""
        return SarQualityGateResult(
            passed=False,
            policy_version=self._policy.policy_version,
            policy_hash=self._policy_hash,
            reasons=(reason,),
            checks=_EMPTY_CHECKS,
            citation_metrics=_EMPTY_METRICS,
            fallback_required=reason not in _terminal_reasons(available),
        )

    def evaluate(
        self,
        content: SarDraftContent,
        *,
        available: tuple[SarCitation, ...],
        catalog: SarEvidenceCatalog,
        finish_reason: str | None = None,
        available_evidence_refs: Collection[str] | None = None,
    ) -> SarQualityGateResult:
        """Evaluate one ungrounded candidate against every enabled deterministic rule."""
        refs = (
            frozenset(available_evidence_refs)
            if available_evidence_refs is not None
            else catalog.refs
        )
        checks = evaluate_draft_checks(content, available, available_evidence_refs=refs)
        produced = _produced_citation_ids(content)
        metrics = citation_precision_recall(produced, [item.citation for item in available], ())
        duplicates = _duplicate_ids(content)
        mismatches = _fact_mismatches(content, catalog)
        unmapped = _unmapped_narrative_spans(content, catalog, available)
        missing_elements = _missing_fincen_elements(content)
        reasons = self._reasons(
            checks=checks,
            content=content,
            refs=refs,
            produced=produced,
            duplicates=duplicates,
            mismatches=mismatches,
            unmapped=unmapped,
            missing_elements=missing_elements,
            finish_reason=finish_reason,
        )
        return SarQualityGateResult(
            passed=not reasons,
            policy_version=self._policy.policy_version,
            policy_hash=self._policy_hash,
            reasons=reasons,
            checks=checks,
            citation_metrics=metrics,
            fabricated_citation_ids=checks.fabricated_citation_ids,
            duplicate_citation_ids=duplicates,
            unsupported_claim_indexes=checks.unsupported_claim_indexes,
            mismatched_facts=mismatches,
            unmapped_narrative_spans=unmapped,
            missing_fincen_elements=missing_elements,
            fallback_required=bool(reasons)
            and not _terminal_reasons(available).intersection(reasons),
        )

    def _reasons(  # noqa: PLR0913 - one explicit argument per evaluated rule input.
        self,
        *,
        checks: DeterministicReviewChecks,
        content: SarDraftContent,
        refs: frozenset[str],
        produced: tuple[str, ...],
        duplicates: tuple[str, ...],
        mismatches: tuple[SarFactMismatch, ...],
        unmapped: tuple[int, ...],
        missing_elements: tuple[str, ...],
        finish_reason: str | None,
    ) -> tuple[SarGateReason, ...]:
        """Map the computed findings onto ordered, stable rejection reason codes."""
        policy = self._policy
        reasons: list[SarGateReason] = []
        if not refs:
            reasons.append(SarGateReason.EVIDENCE_EMPTY)
        if policy.require_citation and not produced:
            reasons.append(SarGateReason.NO_CITATIONS)
        if checks.fabricated_citation_ids:
            reasons.append(SarGateReason.CITATION_FABRICATED)
        if duplicates and not policy.allow_duplicate_citations:
            reasons.append(SarGateReason.CITATION_DUPLICATED)
        if policy.require_claim_evidence:
            if not content.claims or not checks.every_claim_has_evidence:
                reasons.append(SarGateReason.CLAIM_MISSING_EVIDENCE)
            if not checks.evidence_refs_are_available:
                reasons.append(SarGateReason.EVIDENCE_REF_UNRESOLVED)
        if policy.require_asserted_fact_match:
            if mismatches:
                reasons.append(SarGateReason.ASSERTED_FACT_MISMATCH)
            if unmapped:
                reasons.append(SarGateReason.UNMAPPED_NARRATIVE_FACT)
        if policy.require_fincen_elements and missing_elements:
            reasons.append(SarGateReason.FINCEN_ELEMENT_MISSING)
        if policy.fail_on_truncation and finish_reason == _TRUNCATED_FINISH_REASON:
            reasons.append(SarGateReason.OUTPUT_TRUNCATED)
        return tuple(reasons)


def _terminal_reasons(available: tuple[SarCitation, ...]) -> frozenset[SarGateReason]:
    """Return the reasons no later tier could clear for this exact case."""
    return _ALWAYS_TERMINAL if available else _UNCITABLE_TERMINAL


def evaluate_sar_quality(  # noqa: PLR0913 - one explicit argument per evaluated input.
    gate: SarQualityGate,
    raw_text: str,
    *,
    available: tuple[SarCitation, ...],
    catalog: SarEvidenceCatalog,
    finish_reason: str | None = None,
    available_evidence_refs: Collection[str] | None = None,
) -> SarQualityGateResult:
    """Judge one RAW completion with the shipped gate, treating unparseable output as rejected."""
    try:
        content = parse_only(raw_text)
    except SarSchemaError:
        return gate.rejected(SarGateReason.SCHEMA_INVALID, available=available)
    return gate.evaluate(
        content,
        available=available,
        catalog=catalog,
        finish_reason=finish_reason,
        available_evidence_refs=available_evidence_refs,
    )


def _produced_citation_ids(content: SarDraftContent) -> tuple[str, ...]:
    """Return every citation id the draft produced, in stable order, duplicates retained."""
    return (
        *content.cited_regulations,
        *(citation_id for claim in content.claims for citation_id in claim.citation_ids),
    )


def _duplicate_ids(content: SarDraftContent) -> tuple[str, ...]:
    """Return ids repeated WITHIN one citation list, in first-seen order."""
    duplicates: list[str] = []
    for citation_list in (content.cited_regulations, *(c.citation_ids for c in content.claims)):
        seen: set[str] = set()
        for item in citation_list:
            if item in seen and item not in duplicates:
                duplicates.append(item)
            seen.add(item)
    return tuple(duplicates)


def _fact_mismatches(
    content: SarDraftContent, catalog: SarEvidenceCatalog
) -> tuple[SarFactMismatch, ...]:
    """Compare every asserted fact to its catalog value by canonical typed equality."""
    mismatches: list[SarFactMismatch] = []
    for index, claim in enumerate(content.claims):
        for asserted in claim.asserted_facts:
            fact = catalog.get(asserted.ref)
            if fact is None:
                mismatches.append(
                    SarFactMismatch(claim_index=index, ref=asserted.ref, asserted=asserted.value)
                )
                continue
            canonical = canonical_fact_value(fact.kind, asserted.value)
            if canonical != fact.value:
                mismatches.append(
                    SarFactMismatch(
                        claim_index=index,
                        ref=asserted.ref,
                        expected=fact.value,
                        asserted=canonical or "",
                    )
                )
    return tuple(mismatches)


def _unmapped_narrative_spans(
    content: SarDraftContent,
    catalog: SarEvidenceCatalog,
    available: tuple[SarCitation, ...],
) -> tuple[int, ...]:
    """Return indexes of objective narrative values that resolve to no asserted catalog fact."""
    allowed = _allowed_narrative_values(content, catalog)
    text = " ".join((content.subject, content.narrative, *(s.body for s in content.sections)))
    # Citation ids and provision titles are trusted verbatim corpus text supplied BY the backend,
    # not values the model asserted, and several carry numbers ("... exceeding $10,000"). Remove
    # them before scanning so quoting the evidence correctly is never read as an unmapped fact.
    for citation in available:
        text = text.replace(citation.citation, " ").replace(citation.title, " ")
    unmapped: list[int] = []
    for index, match in enumerate(_OBJECTIVE_VALUE_RE.finditer(text)):
        if not _resolves(match.group(0), allowed):
            unmapped.append(index)
    return tuple(unmapped)


def _allowed_narrative_values(
    content: SarDraftContent, catalog: SarEvidenceCatalog
) -> frozenset[str]:
    """Collect the canonical values the draft's asserted facts authorise the narrative to state."""
    allowed: set[str] = set()
    for claim in content.claims:
        for asserted in claim.asserted_facts:
            fact = catalog.get(asserted.ref)
            if fact is None:
                continue
            allowed.add(fact.value)
            allowed.update(_canonical_forms(fact.display))
    return frozenset(allowed)


def _canonical_forms(raw: str) -> frozenset[str]:
    """Return every canonical form one rendered value may legitimately appear as."""
    forms = {raw}
    for match in _OBJECTIVE_VALUE_RE.finditer(raw):
        forms.update(_normalisations(match.group(0)))
    forms.update(_normalisations(raw))
    return frozenset(form for form in forms if form)


def _normalisations(value: str) -> frozenset[str]:
    """Normalise one span as both a number and an instant; unusable forms are dropped."""
    candidates = (
        canonical_fact_value(SarFactKind.NUMBER, value),
        canonical_fact_value(SarFactKind.MONEY, value),
        canonical_fact_value(SarFactKind.INSTANT, value),
    )
    return frozenset(item for item in candidates if item is not None)


def _resolves(span: str, allowed: Collection[str]) -> bool:
    """Return whether one objective span matches an authorised canonical value."""
    return span in allowed or bool(_normalisations(span).intersection(allowed))


def _missing_fincen_elements(content: SarDraftContent) -> tuple[str, ...]:
    """Return FinCEN elements with no non-empty section covering them."""
    covered = {
        word
        for section in content.sections
        if section.body.strip()
        for word in _WORD_RE.findall(section.heading.casefold())
    }
    return tuple(element.value for element in SarFincenElement if element.value not in covered)

"""Summary: Deterministic per-output and aggregate SAR benchmark quality measurements.

Key classes:
- CaseQuality: schema, citation, fact, abstention, truncation, and support result for one draft.
- QualitySummary: aggregate quality rates and pre-filter fabrication counts for one level or arm.

Key functions:
- evidence_catalog: rebuild one case's production evidence catalog, or an empty one for v1.
- case_verdict: judge one persisted output with the shipped gate over a case's vocabulary.
- evaluate_case: score one raw model output against its closed case expectations.
- summarize_quality: aggregate aligned measurements and cases.

Notes:
- Fabricated references are counted from ungrounded model JSON before any filtering can hide
  them.
- Abstention correctness is None, never 1.0, when no abstention case was evaluated. A scenario
  run accepts only served drafts, so a default of 1.0 published a perfect score for something
  the run never measured — exactly the self-agreeing metric release 0.5.0 exists to remove.
- The pass/fail half of `useful` is the SHIPPED gate (`evaluate_sar_quality`), not a locally
  re-derived predicate (AD-4.1): a draft the benchmark calls useful is one production would have
  served. What stays local is only what the gate cannot know at runtime — expected-citation recall
  and required-fact coverage need the ground truth a benchmark case carries and a request does not.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import mean

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from fraudlens_backend.sar.egress import EgressPolicy, load_egress_policy, project_for_model
from fraudlens_backend.sar.evidence import SarEvidenceCatalog, build_evidence_catalog
from fraudlens_backend.sar.quality_gate import SarQualityGate, evaluate_sar_quality
from fraudlens_backend.sar.schema import SarSchemaError, parse_content
from fraudlens_ml.evaluation.citations import citation_precision_recall, required_fact_coverage
from fraudlens_ml.sar import SarCitation, SarGateReason, SarQualityGateResult
from lib.vllm_bench.config import QualityConfig
from lib.vllm_bench.state import BenchmarkCase, RequestMeasurement

_MODEL_CONFIG = ConfigDict(
    frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True
)
# A protocol-v1 case carries only ground-truth citation and fact expectations, so its asserted-fact
# rules have nothing to judge; a v2+ case carries the production SAR input, whose catalog is
# rebuilt through the production projection exactly as the drafter does at request time.
_EMPTY_CATALOG = SarEvidenceCatalog(facts=())


class CaseQuality(BaseModel):
    """Deterministic quality outcome for one raw synthetic SAR response."""

    model_config = _MODEL_CONFIG

    case_id: str = Field(..., min_length=1, description="Case identity.")
    schema_valid: bool = Field(..., description="Whether output matches SarDraftContent.")
    reference_validity: float = Field(..., ge=0, le=1, description="Produced refs in offered set.")
    citation_recall: float = Field(..., ge=0, le=1, description="Expected refs produced.")
    required_fact_coverage: float = Field(..., ge=0, le=1, description="Required facts present.")
    abstention_correct: bool | None = Field(default=None, description="Evidence-free abstention.")
    fabricated_reference_attempts: int = Field(..., ge=0, description="Pre-filter fabricated ids.")
    truncated: bool = Field(..., description="Whether finish reason reports length truncation.")
    unsupported_claim_flags: int = Field(
        ..., ge=0, description="Deterministically unsupported claims."
    )
    useful: bool = Field(..., description="Schema/reference/fact/support quality pass.")


class QualitySummary(BaseModel):
    """Aggregate deterministic quality measurements."""

    model_config = _MODEL_CONFIG

    evaluated: int = Field(..., ge=0, description="Outputs evaluated.")
    schema_valid_rate: float = Field(..., ge=0, le=1, description="Schema-valid output rate.")
    reference_validity: float = Field(..., ge=0, le=1, description="Mean reference validity.")
    citation_recall: float = Field(..., ge=0, le=1, description="Mean expected-reference recall.")
    required_fact_coverage: float = Field(..., ge=0, le=1, description="Mean fact coverage.")
    abstention_correctness: float | None = Field(
        ...,
        ge=0,
        le=1,
        description="Evidence-free accuracy, or None when no abstention case was evaluated.",
    )
    fabricated_reference_attempts: int = Field(..., ge=0, description="Fabricated-id total.")
    truncation_rate: float = Field(..., ge=0, le=1, description="Length-finish rate.")
    unsupported_claim_flags: int = Field(..., ge=0, description="Unsupported-claim total.")
    useful_count: int = Field(..., ge=0, description="Quality-passing draft count.")


def _available(ids: Sequence[str]) -> tuple[SarCitation, ...]:
    """Build closed-vocabulary citation objects for deterministic membership checks."""
    return tuple(
        SarCitation(citation=item, title="available", source="public", snippet="available")
        for item in ids
    )


def evidence_catalog(case: BenchmarkCase, policy: EgressPolicy | None = None) -> SarEvidenceCatalog:
    """Rebuild the production evidence catalog for one case; empty for a protocol-v1 corpus."""
    if case.sar_input is None:
        return _EMPTY_CATALOG
    return build_evidence_catalog(project_for_model(case.sar_input, policy or load_egress_policy()))


def case_verdict(
    case: BenchmarkCase,
    measurement: RequestMeasurement,
    gate: SarQualityGate,
    catalog: SarEvidenceCatalog | None = None,
) -> SarQualityGateResult:
    """Judge one persisted output with the shipped gate over this case's closed vocabulary."""
    if measurement.error_code is not None:
        return gate.rejected(SarGateReason.SCHEMA_INVALID)
    return evaluate_sar_quality(
        gate,
        measurement.content,
        available=_available(case.offered_citation_ids),
        catalog=catalog if catalog is not None else evidence_catalog(case),
        finish_reason=measurement.finish_reason,
        available_evidence_refs=case.available_evidence_refs,
    )


def evaluate_case(
    case: BenchmarkCase,
    measurement: RequestMeasurement,
    policy: QualityConfig,
    gate: SarQualityGate,
    catalog: SarEvidenceCatalog | None = None,
) -> CaseQuality:
    """Evaluate one raw output before citation grounding or rendering can alter it."""
    if measurement.error_code is not None:
        return CaseQuality(
            case_id=case.case_id,
            schema_valid=False,
            reference_validity=0.0,
            citation_recall=0.0,
            required_fact_coverage=0.0,
            abstention_correct=False if case.case_set == "abstention" else None,
            fabricated_reference_attempts=0,
            truncated=False,
            unsupported_claim_flags=0,
            useful=False,
        )
    try:
        content = parse_content(measurement.content)
    except SarSchemaError:
        return CaseQuality(
            case_id=case.case_id,
            schema_valid=False,
            reference_validity=0.0,
            citation_recall=0.0,
            required_fact_coverage=0.0,
            abstention_correct=False if case.case_set == "abstention" else None,
            fabricated_reference_attempts=0,
            truncated=measurement.finish_reason == "length",
            unsupported_claim_flags=0,
            useful=False,
        )
    produced = tuple(
        dict.fromkeys(
            (
                *content.cited_regulations,
                *(citation for claim in content.claims for citation in claim.citation_ids),
            )
        )
    )
    citation = citation_precision_recall(
        produced, case.offered_citation_ids, case.expected_citation_ids
    )
    facts = required_fact_coverage(case.required_facts, measurement.content)
    verdict = case_verdict(case, measurement, gate, catalog)
    checks = verdict.checks
    abstention = not produced and not content.claims if case.case_set == "abstention" else None
    truncated = measurement.finish_reason == "length"
    useful = (
        verdict.passed
        and citation.precision >= policy.reference_validity_min
        and facts.coverage >= policy.coverage_warn_min
        and abstention is not False
    )
    return CaseQuality(
        case_id=case.case_id,
        schema_valid=True,
        reference_validity=citation.precision,
        citation_recall=citation.recall,
        required_fact_coverage=facts.coverage,
        abstention_correct=abstention,
        fabricated_reference_attempts=len(citation.fabricated_ids),
        truncated=truncated,
        unsupported_claim_flags=len(checks.unsupported_claim_indexes),
        useful=useful,
    )


def summarize_quality(
    cases: Mapping[str, BenchmarkCase],
    measurements: Sequence[RequestMeasurement],
    policy: QualityConfig,
    gate: SarQualityGate,
) -> tuple[QualitySummary, tuple[CaseQuality, ...]]:
    """Aggregate deterministic quality over aligned case measurements."""
    egress = load_egress_policy()
    catalogs = {
        case_id: evidence_catalog(cases[case_id], egress)
        for case_id in {item.case_id for item in measurements}
    }
    results = tuple(
        evaluate_case(cases[item.case_id], item, policy, gate, catalogs[item.case_id])
        for item in measurements
    )
    if not results:
        return (
            QualitySummary(
                evaluated=0,
                schema_valid_rate=0,
                reference_validity=0,
                citation_recall=0,
                required_fact_coverage=0,
                abstention_correctness=None,
                fabricated_reference_attempts=0,
                truncation_rate=0,
                unsupported_claim_flags=0,
                useful_count=0,
            ),
            (),
        )
    abstentions = [
        item.abstention_correct for item in results if item.abstention_correct is not None
    ]
    summary = QualitySummary(
        evaluated=len(results),
        schema_valid_rate=mean(item.schema_valid for item in results),
        reference_validity=mean(item.reference_validity for item in results),
        citation_recall=mean(item.citation_recall for item in results),
        required_fact_coverage=mean(item.required_fact_coverage for item in results),
        abstention_correctness=mean(abstentions) if abstentions else None,
        fabricated_reference_attempts=sum(item.fabricated_reference_attempts for item in results),
        truncation_rate=mean(item.truncated for item in results),
        unsupported_claim_flags=sum(item.unsupported_claim_flags for item in results),
        useful_count=sum(item.useful for item in results),
    )
    return summary, results

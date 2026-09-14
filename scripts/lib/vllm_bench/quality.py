"""Summary: Deterministic per-output and aggregate SAR benchmark quality measurements.

Key classes:
- CaseQuality: schema, citation, fact, abstention, truncation, and support result for one draft.
- QualitySummary: aggregate quality rates and pre-filter fabrication counts for one level or arm.

Key functions:
- evaluate_case: score one raw model output against its closed case expectations.
- summarize_quality: aggregate aligned measurements and cases.

Notes:
- Fabricated references are counted from ungrounded model JSON before any filtering can hide them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import mean

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from fraudlens_backend.agents.checks import evaluate_draft_checks
from fraudlens_backend.sar.schema import SarSchemaError, parse_content
from fraudlens_ml.evaluation.citations import citation_precision_recall, required_fact_coverage
from fraudlens_ml.sar import SarCitation
from lib.vllm_bench.config import QualityConfig
from lib.vllm_bench.state import BenchmarkCase, RequestMeasurement

_MODEL_CONFIG = ConfigDict(
    frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True
)


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
    abstention_correctness: float = Field(..., ge=0, le=1, description="Evidence-free accuracy.")
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


def evaluate_case(
    case: BenchmarkCase,
    measurement: RequestMeasurement,
    policy: QualityConfig,
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
    checks = evaluate_draft_checks(
        content,
        _available(case.offered_citation_ids),
        available_evidence_refs=case.available_evidence_refs,
    )
    abstention = not produced and not content.claims if case.case_set == "abstention" else None
    truncated = measurement.finish_reason == "length"
    useful = (
        citation.precision >= policy.reference_validity_min
        and facts.coverage >= policy.coverage_warn_min
        and checks.passed
        and not truncated
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
) -> tuple[QualitySummary, tuple[CaseQuality, ...]]:
    """Aggregate deterministic quality over aligned case measurements."""
    results = tuple(evaluate_case(cases[item.case_id], item, policy) for item in measurements)
    if not results:
        return (
            QualitySummary(
                evaluated=0,
                schema_valid_rate=0,
                reference_validity=0,
                citation_recall=0,
                required_fact_coverage=0,
                abstention_correctness=0,
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
        abstention_correctness=mean(abstentions) if abstentions else 1.0,
        fabricated_reference_attempts=sum(item.fabricated_reference_attempts for item in results),
        truncation_rate=mean(item.truncated for item in results),
        unsupported_claim_flags=sum(item.unsupported_claim_flags for item in results),
        useful_count=sum(item.useful for item in results),
    )
    return summary, results

"""Summary: Strict published report models and metric assembly for the SAR study.
Judge samples are reduced by narrative median, citations/cost/latency/model calls remain
programmatic, and all eight paired metric deltas receive fixed-seed 10k BCa intervals.

Key classes:
- JudgeProvenance: published blind-judge model and prompt protocol.
- ArmProvenance: observed writer, model, prompt, and graph provenance.
- ScenarioArmMetrics: one arm's median judge and programmatic measures.
- ScenarioComparison: the paired measurements for one synthetic scenario.
- ArmSummary: mean measures across one study arm.
- MetricDelta: a parse-time significance-validated paired result.
- StudySummary: both arm aggregates and the eight paired deltas.
- FrontendStudyData: browser-safe aggregate projection bound during publication.
- SarEvalStudyReport: full documentation artifact including judge samples and headline.

Key functions:
- build_study_report: validate inputs and derive the complete report.
- validate_report_binding: enforce config, model, and current prompt lineage.
- frontend_projection: project aggregate-only data for the lazy research page.

Notes:
- Every delta is raw multi-agent minus single-writer; the metric name carries directionality.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from math import isclose
from statistics import mean, median
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from lib.sar_eval.config import (
    SarEvalConfig,
    SarTypology,
    ScenarioVariant,
    validate_config_binding,
)
from lib.sar_eval.judge import (
    ArmJudgeSample,
    JudgePromptTemplate,
    JudgeSample,
    JudgmentArtifact,
    validate_judgment_binding,
)
from lib.sar_eval.metrics import (
    bca_mean_interval,
    pairwise_agreement,
    pairwise_exact_agreement,
)
from lib.sar_eval.runner import ApiArmResult, ApiRunArtifact, Arm
from lib.sar_eval.scenarios import ScenarioArtifact, canonical_run_id

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    extra="forbid",
    alias_generator=to_camel,
    populate_by_name=True,
    allow_inf_nan=False,
)
MetricName = Literal[
    "completenessRate",
    "unsupportedClaims",
    "citationPrecision",
    "citationRecall",
    "fabricatedCitationCount",
    "costUsd",
    "latencyMs",
    "modelCalls",
]
_METRICS: tuple[MetricName, ...] = (
    "completenessRate",
    "unsupportedClaims",
    "citationPrecision",
    "citationRecall",
    "fabricatedCitationCount",
    "costUsd",
    "latencyMs",
    "modelCalls",
)
_MODEL_REFERENCE_PARTS = 3
_HASH_PATTERN = r"^[0-9a-f]{64}$"
_SHA256_HEX_LENGTH = 64


def _model_family(model_ref: str) -> str:
    parts = model_ref.split("/")
    if len(parts) < _MODEL_REFERENCE_PARTS or any(not part for part in parts):
        raise ValueError("model references must include router, family, and model")
    return parts[1]


class JudgeProvenance(BaseModel):
    """Published blind-judge protocol and exact provenance."""

    model_config = _MODEL_CONFIG

    model_id: str = Field(..., min_length=1, description="Judge model reference.")
    model_family: str = Field(..., min_length=1, description="Judge model family.")
    prompt_version: str = Field(..., min_length=1, description="Judge prompt version.")
    prompt_hash: str = Field(..., pattern=_HASH_PATTERN, description="Exact prompt hash.")
    samples_per_narrative: Literal[3] = Field(..., description="Independent samples per narrative.")
    blind: Literal[True] = Field(..., description="Judge never sees workflow identity.")
    order_randomized: Literal[True] = Field(..., description="Candidate order is seeded/shuffled.")

    @model_validator(mode="after")
    def _family_matches_model(self) -> JudgeProvenance:
        if self.model_family != _model_family(self.model_id):
            raise ValueError("judge modelFamily must match the modelId family segment")
        return self


class ArmProvenance(BaseModel):
    """Distinct model and prompt provenance observed for one arm."""

    model_config = _MODEL_CONFIG

    arm: Arm = Field(..., description="Workflow arm.")
    writer_model_id: str = Field(..., min_length=1, description="Model that persisted the SAR.")
    writer_model_family: str = Field(..., min_length=1, description="Writer provider family.")
    model_ids: tuple[str, ...] = Field(..., min_length=1, description="Observed model refs.")
    prompt_versions: tuple[str, ...] = Field(..., min_length=1, description="Observed prompts.")
    prompt_hashes: tuple[str, ...] = Field(..., min_length=1, description="Observed hashes.")
    graph_version: str | None = Field(default=None, description="Graph version for multi-agent.")

    @field_validator("prompt_hashes")
    @classmethod
    def _prompt_hashes_are_sha256(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            len(item) != _SHA256_HEX_LENGTH
            or any(character not in "0123456789abcdef" for character in item)
            for item in value
        ):
            raise ValueError("promptHashes must contain lowercase SHA-256 digests")
        return value

    @model_validator(mode="after")
    def _writer_is_observed(self) -> ArmProvenance:
        if self.writer_model_id not in self.model_ids:
            raise ValueError("writerModelId must be present in modelIds")
        if self.writer_model_family != _model_family(self.writer_model_id):
            raise ValueError("writerModelFamily must match the writerModelId family segment")
        return self


class ScenarioArmMetrics(BaseModel):
    """Median judge and programmatic measurements for one scenario arm."""

    model_config = _MODEL_CONFIG

    completeness_passed: int = Field(..., ge=0, le=5, description="Median passed FinCEN elements.")
    unsupported_claim_count: int = Field(..., ge=0, description="Median unsupported claim count.")
    citation_precision: float = Field(..., ge=0, le=1, description="Expected-citation precision.")
    citation_recall: float = Field(..., ge=0, le=1, description="Expected-citation recall.")
    fabricated_citation_count: int = Field(..., ge=0, description="Ids outside corpus vocabulary.")
    cost_usd: float = Field(..., ge=0, description="Persisted drafting cost.")
    latency_ms: int = Field(
        ..., ge=0, description="Persisted investigation created-to-updated duration."
    )
    model_calls: int = Field(..., gt=0, description="Successful provider generations.")
    element_agreement: float = Field(..., ge=0, le=1, description="Element-decision agreement.")
    unsupported_claim_count_agreement: float = Field(
        ..., ge=0, le=1, description="Exact unsupported-claim count agreement."
    )
    unsupported_claim_span_agreement: float = Field(
        ..., ge=0, le=1, description="Exact unsupported-claim span-set agreement."
    )
    agreement: float = Field(..., ge=0, le=1, description="Mean of the three agreement measures.")

    @model_validator(mode="after")
    def _composite_agreement_is_derived(self) -> ScenarioArmMetrics:
        derived = mean(
            (
                self.element_agreement,
                self.unsupported_claim_count_agreement,
                self.unsupported_claim_span_agreement,
            )
        )
        if not isclose(self.agreement, derived, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("agreement must equal the mean of the three agreement measures")
        return self


class ScenarioComparison(BaseModel):
    """Both paired arm aggregates for one synthetic scenario."""

    model_config = _MODEL_CONFIG

    scenario_id: str = Field(..., min_length=1, description="Scenario key.")
    typology: SarTypology = Field(..., description="Synthetic AML pattern.")
    variant: ScenarioVariant = Field(..., description="Evidence-quality variant.")
    single_writer: ScenarioArmMetrics = Field(..., description="Baseline measurements.")
    multi_agent: ScenarioArmMetrics = Field(..., description="Multi-agent measurements.")


class ArmSummary(BaseModel):
    """Mean measurements across all 32 scenarios for one arm."""

    model_config = _MODEL_CONFIG

    arm: Arm = Field(..., description="Workflow arm.")
    completeness_rate: float = Field(..., ge=0, le=1, description="Mean completeness / five.")
    unsupported_claims: float = Field(..., ge=0, description="Mean unsupported claims.")
    citation_precision: float = Field(..., ge=0, le=1, description="Mean citation precision.")
    citation_recall: float = Field(..., ge=0, le=1, description="Mean citation recall.")
    fabricated_citation_count: float = Field(..., ge=0, description="Mean fabricated count.")
    cost_usd: float = Field(..., ge=0, description="Mean drafting cost.")
    latency_ms: float = Field(..., ge=0, description="Mean persisted run duration.")
    model_calls: float = Field(..., gt=0, description="Mean successful generations.")
    element_agreement: float = Field(..., ge=0, le=1, description="Mean element agreement.")
    unsupported_claim_count_agreement: float = Field(
        ..., ge=0, le=1, description="Mean unsupported-claim count agreement."
    )
    unsupported_claim_span_agreement: float = Field(
        ..., ge=0, le=1, description="Mean unsupported-claim span-set agreement."
    )
    agreement: float = Field(..., ge=0, le=1, description="Mean judge agreement.")

    @model_validator(mode="after")
    def _composite_agreement_is_derived(self) -> ArmSummary:
        derived = mean(
            (
                self.element_agreement,
                self.unsupported_claim_count_agreement,
                self.unsupported_claim_span_agreement,
            )
        )
        if not isclose(self.agreement, derived, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("agreement must equal the mean of the three agreement measures")
        return self


class MetricDelta(BaseModel):
    """Multi-agent-minus-single-writer mean delta with a paired BCa interval."""

    model_config = _MODEL_CONFIG

    metric: MetricName = Field(..., description="Measured field.")
    point_estimate: float = Field(..., description="Mean paired delta.")
    ci_lower: float = Field(..., description="Lower BCa bound.")
    ci_upper: float = Field(..., description="Upper BCa bound.")
    significant: bool = Field(..., description="Whether the interval excludes zero.")

    @model_validator(mode="after")
    def _interval_and_significance(self) -> MetricDelta:
        if self.ci_lower > self.ci_upper:
            raise ValueError("metric interval lower bound exceeds upper bound")
        derived = self.ci_lower > 0 or self.ci_upper < 0
        if self.significant != derived:
            raise ValueError("significant must equal whether the interval excludes zero")
        return self


class StudySummary(BaseModel):
    """Per-arm aggregate values and all required paired deltas."""

    model_config = _MODEL_CONFIG

    arms: tuple[ArmSummary, ArmSummary] = Field(..., description="Baseline then multi-agent.")
    deltas: tuple[MetricDelta, ...] = Field(
        ..., min_length=8, max_length=8, description="All metrics."
    )

    @model_validator(mode="after")
    def _complete(self) -> StudySummary:
        if tuple(item.arm for item in self.arms) != ("single_writer", "multi_agent"):
            raise ValueError("summary arms must be baseline then multi-agent")
        if tuple(item.metric for item in self.deltas) != _METRICS:
            raise ValueError("summary deltas must contain every metric in canonical order")
        return self


class FrontendStudyData(BaseModel):
    """Strict browser-safe projection, hash-bound to the full report."""

    model_config = _MODEL_CONFIG

    report_sha256: str = Field(..., pattern=_HASH_PATTERN, description="Full report hash.")
    run_id: str = Field(..., min_length=1, description="Evaluation run id.")
    seed: int = Field(..., ge=0, description="Protocol seed.")
    synthetic_data: Literal[True] = Field(..., description="Mandatory synthetic-data disclosure.")
    scenario_count: Literal[32] = Field(..., description="Fixed protocol scenario count.")
    bootstrap_resamples: Literal[10000] = Field(..., description="Fixed BCa resamples.")
    judge: JudgeProvenance = Field(..., description="Judge protocol.")
    arm_provenance: tuple[ArmProvenance, ArmProvenance] = Field(..., description="Arm provenance.")
    summary: StudySummary = Field(..., description="Aggregate results.")
    scenarios: tuple[ScenarioComparison, ...] = Field(
        ..., min_length=32, max_length=32, description="Rows."
    )

    @model_validator(mode="after")
    def _judge_writer_family_mismatch(self) -> FrontendStudyData:
        if tuple(item.arm for item in self.arm_provenance) != (
            "single_writer",
            "multi_agent",
        ):
            raise ValueError("arm provenance must be baseline then multi-agent")
        if any(item.writer_model_family == self.judge.model_family for item in self.arm_provenance):
            raise ValueError("judge family must differ from every arm writer family")
        _require_complete_scenario_matrix(self.scenarios)
        return self


class SarEvalStudyReport(BaseModel):
    """Full documentation report with quote-level judge evidence and disclosures."""

    model_config = _MODEL_CONFIG

    run_id: str = Field(..., min_length=1, description="Evaluation run id.")
    config_sha256: str = Field(..., pattern=_HASH_PATTERN, description="Protocol config hash.")
    seed: int = Field(..., ge=0, description="Protocol seed.")
    synthetic_data: Literal[True] = Field(..., description="Mandatory synthetic-data disclosure.")
    scenario_count: Literal[32] = Field(..., description="Fixed scenario count.")
    bootstrap_resamples: Literal[10000] = Field(..., description="Fixed paired BCa draws.")
    headline: str = Field(..., min_length=1, description="Mechanically sign-derived headline.")
    judge: JudgeProvenance = Field(..., description="Judge protocol.")
    arm_provenance: tuple[ArmProvenance, ArmProvenance] = Field(..., description="Arm provenance.")
    summary: StudySummary = Field(..., description="Aggregate measurements.")
    scenarios: tuple[ScenarioComparison, ...] = Field(
        ..., min_length=32, max_length=32, description="Rows."
    )
    judge_samples: tuple[JudgeSample, ...] = Field(
        ..., min_length=96, max_length=96, description="Evidence."
    )
    api_spent_usd: Decimal = Field(..., ge=0, description="Observed API drafting spend.")
    api_reserved_usd: Decimal = Field(
        ..., ge=0, description="Cumulative conservative API attempt reservations."
    )
    judge_spent_usd: Decimal = Field(..., ge=0, description="Observed judge spend.")
    disclosures: tuple[str, ...] = Field(..., min_length=1, description="Study limitations.")

    @model_validator(mode="after")
    def _judge_writer_family_mismatch(self) -> SarEvalStudyReport:
        if self.api_spent_usd > self.api_reserved_usd:
            raise ValueError("API observed spend cannot exceed cumulative reservations")
        if tuple(item.arm for item in self.arm_provenance) != (
            "single_writer",
            "multi_agent",
        ):
            raise ValueError("arm provenance must be baseline then multi-agent")
        if any(item.writer_model_family == self.judge.model_family for item in self.arm_provenance):
            raise ValueError("judge family must differ from every arm writer family")
        _require_complete_scenario_matrix(self.scenarios)
        expected_samples = {
            (scenario.scenario_id, sample_index)
            for scenario in self.scenarios
            for sample_index in (1, 2, 3)
        }
        observed_samples = {
            (sample.scenario_id, sample.sample_index) for sample in self.judge_samples
        }
        if observed_samples != expected_samples or len(observed_samples) != len(self.judge_samples):
            raise ValueError("judge samples must cover every published scenario three times")
        if self.headline != _headline(self.summary.deltas):
            raise ValueError("headline must be mechanically derived from the completeness delta")
        return self


def _require_complete_scenario_matrix(rows: tuple[ScenarioComparison, ...]) -> None:
    keys = {(item.typology, item.variant) for item in rows}
    expected = {(typology, variant) for typology in SarTypology for variant in ScenarioVariant}
    if keys != expected or len({item.scenario_id for item in rows}) != len(rows):
        raise ValueError("published rows must contain the unique canonical 8 x 4 scenario matrix")


def _sample_for(sample: JudgeSample, arm: Arm) -> ArmJudgeSample:
    return next(item for item in sample.arms if item.arm == arm)


def _scenario_arm(
    arm: Arm,
    run: ApiArmResult,
    samples: list[JudgeSample],
    expected: set[str],
    corpus: set[str],
) -> ScenarioArmMetrics:
    arm_samples = [_sample_for(sample, arm) for sample in samples]
    completeness = [sum(item.present for item in sample.elements) for sample in arm_samples]
    unsupported = [len(sample.unsupported_claims) for sample in arm_samples]
    element_vectors = tuple(
        tuple(item.present for item in sample.elements) for sample in arm_samples
    )
    unsupported_counts = tuple(len(sample.unsupported_claims) for sample in arm_samples)
    unsupported_span_sets = tuple(
        frozenset(claim.quoted_span for claim in sample.unsupported_claims)
        for sample in arm_samples
    )
    element_agreement = pairwise_agreement(element_vectors)
    unsupported_count_agreement = pairwise_exact_agreement(unsupported_counts)
    unsupported_span_agreement = pairwise_exact_agreement(unsupported_span_sets)
    cited = set(run.citation_ids)
    true_positive = len(cited & expected)
    precision = true_positive / len(cited) if cited else 0.0
    recall = true_positive / len(expected) if expected else 1.0
    return ScenarioArmMetrics(
        completeness_passed=int(median(completeness)),
        unsupported_claim_count=int(median(unsupported)),
        citation_precision=precision,
        citation_recall=recall,
        fabricated_citation_count=len(cited - corpus),
        cost_usd=float(run.cost_usd),
        latency_ms=run.latency_ms,
        model_calls=run.model_calls,
        element_agreement=element_agreement,
        unsupported_claim_count_agreement=unsupported_count_agreement,
        unsupported_claim_span_agreement=unsupported_span_agreement,
        agreement=mean(
            (element_agreement, unsupported_count_agreement, unsupported_span_agreement)
        ),
    )


def _metric(item: ScenarioArmMetrics, metric: MetricName) -> float:
    mapping = {
        "completenessRate": item.completeness_passed / 5.0,
        "unsupportedClaims": float(item.unsupported_claim_count),
        "citationPrecision": item.citation_precision,
        "citationRecall": item.citation_recall,
        "fabricatedCitationCount": float(item.fabricated_citation_count),
        "costUsd": item.cost_usd,
        "latencyMs": float(item.latency_ms),
        "modelCalls": float(item.model_calls),
    }
    return mapping[metric]


def _summary(arm: Arm, rows: tuple[ScenarioComparison, ...]) -> ArmSummary:
    values = [row.single_writer if arm == "single_writer" else row.multi_agent for row in rows]
    return ArmSummary(
        arm=arm,
        completeness_rate=mean(item.completeness_passed / 5 for item in values),
        unsupported_claims=mean(item.unsupported_claim_count for item in values),
        citation_precision=mean(item.citation_precision for item in values),
        citation_recall=mean(item.citation_recall for item in values),
        fabricated_citation_count=mean(item.fabricated_citation_count for item in values),
        cost_usd=mean(item.cost_usd for item in values),
        latency_ms=mean(item.latency_ms for item in values),
        model_calls=mean(item.model_calls for item in values),
        element_agreement=mean(item.element_agreement for item in values),
        unsupported_claim_count_agreement=mean(
            item.unsupported_claim_count_agreement for item in values
        ),
        unsupported_claim_span_agreement=mean(
            item.unsupported_claim_span_agreement for item in values
        ),
        agreement=mean(item.agreement for item in values),
    )


def _headline(deltas: tuple[MetricDelta, ...]) -> str:
    completeness = next(item for item in deltas if item.metric == "completenessRate")
    if completeness.point_estimate > 0:
        verb = "improved"
    elif completeness.point_estimate < 0:
        verb = "reduced"
    else:
        verb = "did not change"
    return (
        f"Multi-agent drafting {verb} mean FinCEN narrative completeness by "
        f"{completeness.point_estimate:+.3f} (95% BCa CI "
        f"[{completeness.ci_lower:+.3f}, {completeness.ci_upper:+.3f}])."
    )


def _provenance(runs: ApiRunArtifact, arm: Arm) -> ArmProvenance:
    rows = [item for item in runs.results if item.arm == arm]
    graph_versions = {item.graph_version for item in rows if item.graph_version}
    writer_model_ids = {item.writer_model_id for item in rows}
    if len(writer_model_ids) != 1:
        raise ValueError("arm observations must agree on one writer model id")
    writer_model_id = next(iter(writer_model_ids))
    writer_family = _model_family(writer_model_id)
    if arm == "multi_agent" and len(graph_versions) != 1:
        raise ValueError("multi-agent observations must agree on one graph version")
    return ArmProvenance(
        arm=arm,
        writer_model_id=writer_model_id,
        writer_model_family=writer_family,
        model_ids=tuple(sorted({value for item in rows for value in item.model_ids})),
        prompt_versions=tuple(sorted({value for item in rows for value in item.prompt_versions})),
        prompt_hashes=tuple(sorted({value for item in rows for value in item.prompt_hashes})),
        graph_version=next(iter(graph_versions), None),
    )


def build_study_report(
    scenarios: ScenarioArtifact,
    runs: ApiRunArtifact,
    judgments: JudgmentArtifact,
    config: SarEvalConfig,
    *,
    corpus_citation_ids: set[str],
) -> SarEvalStudyReport:
    """Derive the complete typed report from three complete stage artifacts."""
    validate_judgment_binding(
        judgments,
        scenarios=scenarios,
        runs=runs,
        config=config,
    )
    expected_citations = {
        citation for scenario in scenarios.scenarios for citation in scenario.expected_citation_ids
    }
    if not expected_citations <= corpus_citation_ids:
        raise ValueError("scenario citations must belong to the committed corpus vocabulary")
    run_map = {(item.scenario_id, item.arm): item for item in runs.results}
    sample_map: dict[str, list[JudgeSample]] = defaultdict(list)
    for sample in judgments.samples:
        sample_map[sample.scenario_id].append(sample)
    rows: list[ScenarioComparison] = []
    for scenario in scenarios.scenarios:
        samples = sorted(sample_map[scenario.scenario_id], key=lambda item: item.sample_index)
        if len(samples) != config.judge.samples_per_narrative:
            raise ValueError("scenario does not have exactly three judge samples")
        expected = set(scenario.expected_citation_ids)
        rows.append(
            ScenarioComparison(
                scenario_id=scenario.scenario_id,
                typology=scenario.typology,
                variant=scenario.variant,
                single_writer=_scenario_arm(
                    "single_writer",
                    run_map[(scenario.scenario_id, "single_writer")],
                    samples,
                    expected,
                    corpus_citation_ids,
                ),
                multi_agent=_scenario_arm(
                    "multi_agent",
                    run_map[(scenario.scenario_id, "multi_agent")],
                    samples,
                    expected,
                    corpus_citation_ids,
                ),
            )
        )
    scenario_rows = tuple(rows)
    deltas: list[MetricDelta] = []
    for offset, metric in enumerate(_METRICS):
        paired = np.array(
            [_metric(row.multi_agent, metric) - _metric(row.single_writer, metric) for row in rows]
        )
        interval = bca_mean_interval(
            paired,
            resamples=config.bootstrap.resamples,
            confidence_level=config.bootstrap.confidence_level,
            seed=config.seed + offset,
        )
        significant = interval.lower > 0 or interval.upper < 0
        deltas.append(
            MetricDelta(
                metric=metric,
                point_estimate=interval.point_estimate,
                ci_lower=interval.lower,
                ci_upper=interval.upper,
                significant=significant,
            )
        )
    summary = StudySummary(
        arms=(_summary("single_writer", scenario_rows), _summary("multi_agent", scenario_rows)),
        deltas=tuple(deltas),
    )
    judge = JudgeProvenance(
        model_id=judgments.model_id,
        model_family=judgments.model_family,
        prompt_version=judgments.prompt_version,
        prompt_hash=judgments.prompt_hash,
        samples_per_narrative=3,
        blind=True,
        order_randomized=True,
    )
    provenance = (_provenance(runs, "single_writer"), _provenance(runs, "multi_agent"))
    report = SarEvalStudyReport(
        run_id=scenarios.run_id,
        config_sha256=scenarios.config_sha256,
        seed=config.seed,
        synthetic_data=True,
        scenario_count=32,
        bootstrap_resamples=10_000,
        headline=_headline(summary.deltas),
        judge=judge,
        arm_provenance=provenance,
        summary=summary,
        scenarios=scenario_rows,
        judge_samples=judgments.samples,
        api_spent_usd=runs.spent_usd,
        api_reserved_usd=runs.reserved_usd,
        judge_spent_usd=judgments.spent_usd,
        disclosures=(
            "All transactions and narratives are synthetic; no real PHI is used.",
            "Judge scores are model-mediated; three-sample agreement is published for stability.",
            (
                "BCa intervals pair the same 32 scenarios and do not establish "
                "production effectiveness."
            ),
        ),
    )
    validate_report_binding(report, config)
    return report


def validate_report_binding(report: SarEvalStudyReport, config: SarEvalConfig) -> None:
    """Require a report to match the loaded config, judge model, and prompt bytes."""
    validate_config_binding(config, report.config_sha256)
    if report.seed != config.seed:
        raise ValueError("report seed does not match the loaded evaluation protocol")
    if report.run_id != canonical_run_id(report.config_sha256, report.seed):
        raise ValueError("report run id is not canonical for its config SHA and seed")
    if report.judge.model_id != config.judge.model:
        raise ValueError("report judge model does not match the loaded evaluation protocol")
    prompt = JudgePromptTemplate.load(config.judge.prompt_id)
    if (
        report.judge.prompt_version != prompt.prompt_version
        or report.judge.prompt_hash != prompt.prompt_hash
    ):
        raise ValueError("report does not match the current versioned judge prompt bytes")


def frontend_projection(report: SarEvalStudyReport, report_sha256: str) -> FrontendStudyData:
    """Project aggregate-only browser data bound to the exact full report bytes."""
    return FrontendStudyData(
        report_sha256=report_sha256,
        run_id=report.run_id,
        seed=report.seed,
        synthetic_data=True,
        scenario_count=32,
        bootstrap_resamples=10_000,
        judge=report.judge,
        arm_provenance=report.arm_provenance,
        summary=report.summary,
        scenarios=report.scenarios,
    )

"""Summary: Strict Pydantic contracts and invariants for published SAR study reports.

Key classes:
- JudgeProvenance: blind-judge protocol provenance.
- ArmProvenance: observed writer and prompt provenance.
- ScenarioArmMetrics: one scenario-arm measurement set.
- ScenarioComparison:
- ArmSummary:
- MetricDelta:
- StudySummary: paired arm aggregates and metric deltas.
- FrontendStudyData:
- SarEvalStudyReport: complete published documentation artifact.

Key functions:
- headline: mechanically derive the report claim from the completeness delta.

Notes:
- Contract validation enforces the complete 8-by-4 scenario matrix and provenance separation.
"""

from __future__ import annotations

from decimal import Decimal
from math import isclose
from statistics import mean
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from lib.sar_eval.config import SarTypology, ScenarioVariant
from lib.sar_eval.judge import JudgeSample
from lib.sar_eval.runner import Arm
from lib.study.binding import model_family

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
_HASH_PATTERN = r"^[0-9a-f]{64}$"
_SHA256_HEX_LENGTH = 64


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
        if self.model_family != model_family(self.model_id):
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
        if self.writer_model_family != model_family(self.writer_model_id):
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
        if self.headline != headline(self.summary.deltas):
            raise ValueError("headline must be mechanically derived from the completeness delta")
        return self


def _require_complete_scenario_matrix(rows: tuple[ScenarioComparison, ...]) -> None:
    keys = {(item.typology, item.variant) for item in rows}
    expected = {(typology, variant) for typology in SarTypology for variant in ScenarioVariant}
    if keys != expected or len({item.scenario_id for item in rows}) != len(rows):
        raise ValueError("published rows must contain the unique canonical 8 x 4 scenario matrix")


def headline(deltas: tuple[MetricDelta, ...]) -> str:
    completeness = next(item for item in deltas if item.metric == "completenessRate")
    if completeness.point_estimate > 0:
        verb = "improved"
    elif completeness.point_estimate < 0:
        verb = "reduced"
    else:
        return (
            "Multi-agent drafting did not change mean FinCEN narrative completeness "
            f"(multi-agent - single-writer delta {completeness.point_estimate:+.3f}; "
            f"95% BCa CI [{completeness.ci_lower:+.3f}, {completeness.ci_upper:+.3f}])."
        )
    return (
        f"Multi-agent drafting {verb} mean FinCEN narrative completeness by "
        f"{abs(completeness.point_estimate):.3f} (multi-agent - single-writer delta "
        f"{completeness.point_estimate:+.3f}; 95% BCa CI "
        f"[{completeness.ci_lower:+.3f}, {completeness.ci_upper:+.3f}])."
    )

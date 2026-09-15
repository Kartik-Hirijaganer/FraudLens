"""Summary: Stable metric-assembly facade for the paired SAR evaluation report.

Key classes:
- (none)

Key functions:
- build_study_report: validate stage artifacts and derive the complete report.
- validate_report_binding: enforce protocol, model, and prompt lineage.
- frontend_projection: derive the hash-bound browser-safe projection.

Notes:
- Report contract classes remain available through explicit re-exports.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean, median

import numpy as np

from lib.sar_eval.config import SarEvalConfig, validate_config_binding
from lib.sar_eval.judge import (
    ArmJudgeSample,
    JudgePromptTemplate,
    JudgeSample,
    JudgmentArtifact,
    validate_judgment_binding,
)
from lib.sar_eval.metrics import bca_mean_interval, pairwise_agreement, pairwise_exact_agreement
from lib.sar_eval.report_contracts import (
    _METRICS,
    ArmProvenance,
    ArmSummary,
    FrontendStudyData,
    JudgeProvenance,
    MetricDelta,
    MetricName,
    SarEvalStudyReport,
    ScenarioArmMetrics,
    ScenarioComparison,
    StudySummary,
    headline,
)
from lib.sar_eval.runner import ApiArmResult, ApiRunArtifact, Arm
from lib.sar_eval.scenarios import ScenarioArtifact, canonical_run_id
from lib.study.binding import model_family

__all__ = [
    "ArmProvenance",
    "ArmSummary",
    "FrontendStudyData",
    "JudgeProvenance",
    "MetricDelta",
    "MetricName",
    "SarEvalStudyReport",
    "ScenarioArmMetrics",
    "ScenarioComparison",
    "StudySummary",
    "build_study_report",
    "frontend_projection",
    "validate_report_binding",
]


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


def _provenance(runs: ApiRunArtifact, arm: Arm) -> ArmProvenance:
    rows = [item for item in runs.results if item.arm == arm]
    graph_versions = {item.graph_version for item in rows if item.graph_version}
    writer_model_ids = {item.writer_model_id for item in rows}
    if len(writer_model_ids) != 1:
        raise ValueError("arm observations must agree on one writer model id")
    writer_model_id = next(iter(writer_model_ids))
    writer_family = model_family(writer_model_id)
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
        headline=headline(summary.deltas),
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

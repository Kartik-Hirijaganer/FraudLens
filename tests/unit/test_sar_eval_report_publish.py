"""Behavioral tests for SAR evaluation reduction, provenance, and bound publication."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from lib.sar_eval.config import DEFAULT_SAR_EVAL_CONFIG, load_sar_eval_config
from lib.sar_eval.judge import (
    ArmJudgeSample,
    ElementName,
    ElementScore,
    JudgePromptTemplate,
    JudgeSample,
    JudgmentArtifact,
    UnsupportedClaim,
)
from lib.sar_eval.publish import publish_report, validate_published_artifacts
from lib.sar_eval.report import (
    FrontendStudyData,
    MetricDelta,
    SarEvalStudyReport,
    build_study_report,
    validate_report_binding,
)
from lib.sar_eval.runner import (
    ApiArmReservation,
    ApiArmResult,
    ApiRunArtifact,
    Arm,
    DurableEvaluationFacts,
    RetrievedRegulationFact,
    ScenarioToolEvidence,
    ToolEvidenceFact,
)
from lib.sar_eval.scenarios import ScenarioArtifact, generate_scenarios

_HASH = "a" * 64
_WRITER = "openrouter/openai/gpt-5-mini"
_JUDGE = "openrouter/anthropic/claude-opus-4.6"


def _scenarios() -> ScenarioArtifact:
    return generate_scenarios(load_sar_eval_config(), DEFAULT_SAR_EVAL_CONFIG.read_bytes())


def _facts() -> DurableEvaluationFacts:
    return DurableEvaluationFacts(
        fraud_probability=0.91,
        risk_band="high",
        rule_hits=({"code": "structuring"},),
        top_features=({"feature": "amount_log", "shapValue": 0.72},),
        model_version="v-test",
        rules_version="rules-test",
        rag_version="rag-test",
        retrieved_regulations=(
            RetrievedRegulationFact(
                chunk_id="fincen-structuring::0",
                doc_id="fincen-structuring",
                citation="31 CFR 1010.314",
                title="Structuring",
                source="FinCEN",
                text="No person shall structure a transaction.",
                score=0.98,
            ),
        ),
    )


def _tool_evidence(scenario_id: str) -> ToolEvidenceFact:
    return ToolEvidenceFact(
        name="transaction_history",
        result={"historicalSyntheticCount": 3, "scenarioKey": scenario_id},
    )


def _runs(scenarios: ScenarioArtifact) -> ApiRunArtifact:
    arms: tuple[Arm, Arm] = ("single_writer", "multi_agent")
    results: list[ApiArmResult] = []
    for scenario in scenarios.scenarios:
        for arm in arms:
            multi = arm == "multi_agent"
            citations = (
                scenario.expected_citation_ids if multi else scenario.expected_citation_ids[:1]
            )
            results.append(
                ApiArmResult(
                    scenario_id=scenario.scenario_id,
                    arm=arm,
                    run_id=f"run-{scenario.scenario_id}-{arm}",
                    narrative=f"Synthetic {arm} narrative for {scenario.scenario_id}.",
                    structured={"synthetic": True},
                    facts=_facts(),
                    citation_ids=citations,
                    writer_model_id=_WRITER,
                    model_ids=(
                        (_WRITER, "openrouter/anthropic/claude-sonnet-4.6") if multi else (_WRITER,)
                    ),
                    prompt_versions=(
                        ("writer@1.0.0", "reviewer@1.0.0") if multi else ("sar@1.0.0",)
                    ),
                    prompt_hashes=((_HASH, "b" * 64) if multi else (_HASH,)),
                    graph_version="agents-v1" if multi else None,
                    cost_usd=Decimal("0.002") if multi else Decimal("0.001"),
                    latency_ms=200 if multi else 100,
                    model_calls=4 if multi else 1,
                    revision_count=0,
                    completed_tool_evidence=(
                        (_tool_evidence(scenario.scenario_id),) if multi else ()
                    ),
                )
            )
    return ApiRunArtifact(
        run_id=scenarios.run_id,
        config_sha256=scenarios.config_sha256,
        authorized_max_usd=Decimal("10"),
        spent_usd=sum((item.cost_usd for item in results), Decimal("0")),
        reserved_usd=Decimal("6.4"),
        reservations=tuple(
            ApiArmReservation(
                scenario_id=scenario.scenario_id,
                arm=arm,
                attempt=1,
                amount_usd=Decimal("0.1"),
            )
            for scenario in scenarios.scenarios
            for arm in arms
        ),
        results=tuple(results),
        scenario_tool_evidence=tuple(
            ScenarioToolEvidence(
                scenario_id=scenario.scenario_id,
                evidence=(_tool_evidence(scenario.scenario_id),),
            )
            for scenario in sorted(scenarios.scenarios, key=lambda item: item.scenario_id)
        ),
    )


def _elements(*, complete: bool) -> tuple[ElementScore, ...]:
    names: tuple[ElementName, ...] = ("who", "what", "when", "where", "why")
    return tuple(
        ElementScore(
            element=name,
            present=complete or name != "why",
            quoted_span=f"synthetic {name}" if complete or name != "why" else None,
        )
        for name in names
    )


def _judgments(scenarios: ScenarioArtifact) -> JudgmentArtifact:
    prompt = JudgePromptTemplate.load(load_sar_eval_config().judge.prompt_id)
    samples: list[JudgeSample] = []
    for scenario in scenarios.scenarios:
        for sample_index in (1, 2, 3):
            samples.append(
                JudgeSample(
                    scenario_id=scenario.scenario_id,
                    sample_index=sample_index,
                    presented_order=("single_writer", "multi_agent"),
                    arms=(
                        ArmJudgeSample(
                            arm="single_writer",
                            unsupported_claims=(
                                UnsupportedClaim(
                                    quoted_span="unsupported assertion",
                                    reason="not present in synthetic evidence",
                                ),
                            ),
                            elements=_elements(complete=False),
                        ),
                        ArmJudgeSample(
                            arm="multi_agent",
                            unsupported_claims=(),
                            elements=_elements(complete=True),
                        ),
                    ),
                )
            )
    return JudgmentArtifact(
        run_id=scenarios.run_id,
        config_sha256=scenarios.config_sha256,
        model_id=_JUDGE,
        model_family="anthropic",
        prompt_version=prompt.prompt_version,
        prompt_hash=prompt.prompt_hash,
        authorized_max_usd=Decimal("10"),
        spent_usd=Decimal("0.01"),
        samples=tuple(samples),
    )


def _report() -> SarEvalStudyReport:
    scenarios = _scenarios()
    return build_study_report(
        scenarios,
        _runs(scenarios),
        _judgments(scenarios),
        load_sar_eval_config(),
        corpus_citation_ids={
            citation
            for scenario in scenarios.scenarios
            for citation in scenario.expected_citation_ids
        },
    )


def test_report_uses_median_judge_scores_programmatic_metrics_and_bca() -> None:
    report = _report()
    first = report.scenarios[0]
    completeness = next(item for item in report.summary.deltas if item.metric == "completenessRate")

    assert report.scenario_count == 32
    assert report.bootstrap_resamples == 10_000
    assert first.single_writer.completeness_passed == 4
    assert first.multi_agent.completeness_passed == 5
    assert first.single_writer.unsupported_claim_count == 1
    assert first.multi_agent.unsupported_claim_count == 0
    assert first.multi_agent.citation_recall == 1.0
    assert first.multi_agent.model_calls == 4
    assert first.single_writer.agreement == 1.0
    assert first.single_writer.element_agreement == 1.0
    assert first.single_writer.unsupported_claim_count_agreement == 1.0
    assert first.single_writer.unsupported_claim_span_agreement == 1.0
    assert completeness.point_estimate == pytest.approx(0.2)
    assert completeness.significant is True
    assert "improved" in report.headline
    assert report.arm_provenance[1].writer_model_id == _WRITER
    assert report.arm_provenance[1].writer_model_family == "openai"
    assert "openrouter/anthropic/claude-sonnet-4.6" in report.arm_provenance[1].model_ids


def test_report_publishes_separate_element_count_and_span_agreement() -> None:
    scenarios = _scenarios()
    judgments = _judgments(scenarios)
    samples = list(judgments.samples)
    original = samples[1]
    single = next(item for item in original.arms if item.arm == "single_writer")
    changed_single = single.model_copy(
        update={
            "unsupported_claims": (
                UnsupportedClaim(
                    quoted_span="different unsupported assertion",
                    reason="not present in synthetic evidence",
                ),
            ),
            "elements": _elements(complete=True),
        }
    )
    samples[1] = original.model_copy(
        update={
            "arms": tuple(
                changed_single if item.arm == "single_writer" else item for item in original.arms
            )
        }
    )
    report = build_study_report(
        scenarios,
        _runs(scenarios),
        judgments.model_copy(update={"samples": tuple(samples)}),
        load_sar_eval_config(),
        corpus_citation_ids={
            citation
            for scenario in scenarios.scenarios
            for citation in scenario.expected_citation_ids
        },
    )
    measured = report.scenarios[0].single_writer

    assert measured.element_agreement == pytest.approx(13 / 15)
    assert measured.unsupported_claim_count_agreement == 1.0
    assert measured.unsupported_claim_span_agreement == pytest.approx(1 / 3)
    assert measured.agreement == pytest.approx((13 / 15 + 1 + 1 / 3) / 3)


def test_report_binding_rejects_config_model_and_prompt_drift() -> None:
    config = load_sar_eval_config()
    report = _report()
    validate_report_binding(report, config)

    with pytest.raises(ValueError, match="config SHA-256"):
        validate_report_binding(report.model_copy(update={"config_sha256": "0" * 64}), config)
    with pytest.raises(ValueError, match="run id is not canonical"):
        validate_report_binding(report.model_copy(update={"run_id": "sar-eval-renamed"}), config)
    changed_judge = report.judge.model_copy(update={"model_id": "openrouter/openai/gpt-5.2"})
    with pytest.raises(ValueError, match="judge model"):
        validate_report_binding(report.model_copy(update={"judge": changed_judge}), config)
    changed_judge = report.judge.model_copy(update={"prompt_hash": "0" * 64})
    with pytest.raises(ValueError, match="current versioned judge prompt"):
        validate_report_binding(report.model_copy(update={"judge": changed_judge}), config)


def test_metric_delta_derives_significance_at_parse_time() -> None:
    with pytest.raises(ValidationError, match="significant must equal"):
        MetricDelta(
            metric="latencyMs",
            point_estimate=1.0,
            ci_lower=0.1,
            ci_upper=2.0,
            significant=False,
        )


def test_report_rejects_judge_writer_family_match() -> None:
    raw = _report().model_dump(mode="json", by_alias=True)
    raw["judge"]["modelId"] = "openrouter/openai/gpt-5.2"
    raw["judge"]["modelFamily"] = "openai"
    with pytest.raises(ValidationError, match="judge family"):
        SarEvalStudyReport.model_validate(raw)

    raw = _report().model_dump(mode="json", by_alias=True)
    raw["headline"] = "Multi-agent drafting always wins."
    with pytest.raises(ValidationError, match="mechanically derived"):
        SarEvalStudyReport.model_validate(raw)


def test_report_rejects_nonfinite_metrics_and_provenance_drift() -> None:
    raw = _report().model_dump(mode="json", by_alias=True)
    raw["judge"]["modelFamily"] = "wrong-family"
    with pytest.raises(ValidationError, match="modelFamily must match"):
        SarEvalStudyReport.model_validate(raw)

    raw = _report().model_dump(mode="json", by_alias=True)
    raw["armProvenance"][0]["promptHashes"] = ["not-a-hash"]
    with pytest.raises(ValidationError, match="SHA-256"):
        SarEvalStudyReport.model_validate(raw)

    raw = _report().model_dump(mode="json", by_alias=True)
    raw["summary"]["arms"][0]["latencyMs"] = float("nan")
    with pytest.raises(ValidationError, match="finite number"):
        SarEvalStudyReport.model_validate(raw)

    raw = _report().model_dump(mode="json", by_alias=True)
    raw["scenarios"][0]["singleWriter"]["agreement"] = 0.0
    with pytest.raises(ValidationError, match="mean of the three agreement"):
        SarEvalStudyReport.model_validate(raw)

    raw = _report().model_dump(mode="json", by_alias=True)
    raw["summary"]["arms"][0]["modelCalls"] = 0.0
    with pytest.raises(ValidationError, match="greater than 0"):
        SarEvalStudyReport.model_validate(raw)


def test_publish_is_atomic_redacted_complete_and_sha_bound(tmp_path: Path) -> None:
    report = _report()
    result = publish_report(
        report,
        docs_dir=tmp_path / "docs",
        frontend_json_path=tmp_path / "frontend" / "sar-multi-agent-study.json",
    )
    published = FrontendStudyData.model_validate_json(result.frontend_json_path.read_text())

    assert result.report_json_path.name == "sar-multi-agent-study.json"
    assert published.report_sha256 == result.report_sha256
    assert published.scenario_count == 32
    assert "judgeSamples" not in result.frontend_json_path.read_text(encoding="utf-8")
    assert not list(tmp_path.rglob("*.tmp"))

    frontend = json.loads(result.frontend_json_path.read_text(encoding="utf-8"))
    frontend["reportSha256"] = "0" * 64
    result.frontend_json_path.write_text(json.dumps(frontend), encoding="utf-8")
    with pytest.raises(ValueError, match="drifted"):
        validate_published_artifacts(result.report_json_path, result.frontend_json_path)


def test_publish_rejects_sensitive_local_path_before_writing(tmp_path: Path) -> None:
    report = _report().model_copy(update={"disclosures": ("/Users/person/private",)})
    with pytest.raises(ValueError, match="redaction scan"):
        publish_report(
            report,
            docs_dir=tmp_path / "docs",
            frontend_json_path=tmp_path / "frontend.json",
        )
    assert not (tmp_path / "docs").exists()


def test_publish_restores_prior_bound_pair_when_second_install_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs_dir = tmp_path / "docs"
    frontend_path = tmp_path / "frontend" / "sar-multi-agent-study.json"
    first = publish_report(_report(), docs_dir=docs_dir, frontend_json_path=frontend_path)
    original_report = first.report_json_path.read_bytes()
    original_frontend = frontend_path.read_bytes()
    real_replace = os.replace

    def fail_frontend_stage(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        if Path(destination) == frontend_path and source_path.name.endswith("sar-eval-stage"):
            raise OSError("simulated second-artifact install failure")
        real_replace(source, destination)

    monkeypatch.setattr("lib.sar_eval.publish.os.replace", fail_frontend_stage)
    changed = _report().model_copy(
        update={"disclosures": (*_report().disclosures, "A valid changed disclosure.")}
    )
    with pytest.raises(OSError, match="second-artifact"):
        publish_report(changed, docs_dir=docs_dir, frontend_json_path=frontend_path)

    assert first.report_json_path.read_bytes() == original_report
    assert frontend_path.read_bytes() == original_frontend
    validate_published_artifacts(first.report_json_path, frontend_path)
    assert not list(tmp_path.rglob("*.sar-eval-stage"))
    assert not list(tmp_path.rglob("*.sar-eval-backup"))

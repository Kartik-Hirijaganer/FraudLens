"""Summary: The pure, deterministic per-step helpers the LangGraph investigation nodes call
(plan §16 Phase 8). Each function maps a step's typed output onto either a PHI-free SSE event
payload (`*_payload`), the persisted `analysis_results` snapshot (`result_record`), or the
assembled `SarInput` the drafter consumes (`build_sar_input`) — and `build_rag_query` turns the
fired rules + non-PHI facts into the retrieval query. Keeping this logic pure (no IO, no DB, no
heavy ML imports) is what lets the orchestration graph stay "pure nodes + injected IO" and makes
every transformation trivially unit-testable. Everything emitted here is PHI-free by construction:
rule hits carry only counts/thresholds/country, SHAP carries feature NAMES + numbers, citations are
escaped, and the SAR input is built from structured non-PHI facts + the masked-by-construction
upstream values (so "PHI masked before the prompt" holds, plan §7.8 / §8.1).

Key classes:
- (none)

Key functions:
- severity_for_band: map a `RiskBand` onto the equal-valued alert `Severity` string.
- rule_hit_json: serialize one `RuleHit` to a PHI-free camelCase JSON dict.
- build_rag_query: build the PHI-free retrieval query from fired rules + non-PHI facts.
- rules_payload: the PHI-free `step.rules.completed` SSE payload (hits + subscore + version).
- scoring_payload: the PHI-free `step.scoring.completed` SSE payload (probability + model).
- shap_payload: the PHI-free `step.shap.completed` SSE payload (top drivers + base value).
- rag_payload: the PHI-free `step.rag.completed` SSE payload (citations + mode).
- completed_payload: the terminal `run.completed` payload (score, band, sar draft id).
- result_record: assemble the immutable `analysis_results` snapshot record.
- build_sar_input: assemble the PHI-free `SarInput` from the rules/score/SHAP/RAG outputs.

Notes:
- `rule_hit_json` dumps in pydantic json mode (Decimal -> string, enums -> value) then renames
  `rule_type` -> `ruleType`, so the persisted/streamed hit is camelCase + JSON-native (no PHI).
- `build_rag_query` always appends a channel/country clause, so a transaction that fired no rules
  still retrieves relevant regulation rather than an empty query (graceful, deterministic).
- The mapping `RiskBand -> Severity` relies on the two enums sharing string values; if they ever
  diverge this raises via the `Severity`-side lookup rather than silently mis-labelling an alert.
"""

from __future__ import annotations

from typing import Any

from fraudlens_core import RiskAssessment, RiskBand, RuleEvaluation
from fraudlens_core.rules.base import RuleHit
from fraudlens_ml.pipeline.events import (
    PipelineInput,
    RagResult,
    ResultRecord,
    ScoreResult,
    ShapResult,
)
from fraudlens_ml.sar import SarInput


def severity_for_band(band: RiskBand) -> str:
    """Return the alert severity string for a band (the two enums share string values)."""
    return band.value


def rule_hit_json(hit: RuleHit) -> dict[str, Any]:
    """Serialize a RuleHit to a PHI-free camelCase JSON dict (Decimal/enum made JSON-native)."""
    dumped = hit.model_dump(mode="json")
    dumped["ruleType"] = dumped.pop("rule_type")
    return dumped


def build_rag_query(evaluation: RuleEvaluation, pipeline_input: PipelineInput) -> str:
    """Build the PHI-free retrieval query from the fired rule reasons + non-PHI facts."""
    parts = [hit.reason for hit in evaluation.hits]
    parts.append(f"{pipeline_input.channel} transaction to {pipeline_input.country}")
    return " ".join(parts).strip()


def rules_payload(evaluation: RuleEvaluation) -> dict[str, Any]:
    """Build the PHI-free `step.rules.completed` payload (hits + subscore + version)."""
    return {
        "subscore": float(evaluation.subscore),
        "rulesVersion": evaluation.rules_version,
        "ruleHits": [rule_hit_json(hit) for hit in evaluation.hits],
        "erroredRules": list(evaluation.errored_rules),
    }


def scoring_payload(score: ScoreResult) -> dict[str, Any]:
    """Build the PHI-free `step.scoring.completed` payload (probability + model version)."""
    return {
        "fraudProbability": score.fraud_probability,
        "modelVersion": score.model_version_label,
        "wasCanary": score.was_canary,
    }


def shap_payload(shap: ShapResult) -> dict[str, Any]:
    """Build the PHI-free `step.shap.completed` payload (top SHAP drivers + base value)."""
    return {
        "baseValue": shap.base_value,
        "topFeatures": [feature.model_dump(by_alias=True) for feature in shap.top_features],
    }


def rag_payload(rag: RagResult) -> dict[str, Any]:
    """Build the PHI-free `step.rag.completed` payload (citations + degradation mode)."""
    return {
        "mode": rag.mode,
        "ragVersion": rag.rag_version,
        "citations": [citation.model_dump(by_alias=True) for citation in rag.citations],
    }


def completed_payload(
    assessment: RiskAssessment, score: ScoreResult, sar_draft_id: str
) -> dict[str, Any]:
    """Build the terminal `run.completed` payload (risk score, band, model, sar draft id)."""
    return {
        "riskScore": assessment.combined_score,
        "riskBand": assessment.risk_band.value,
        "modelVersion": score.model_version_label,
        "sarDraftId": sar_draft_id,
    }


def result_record(
    *,
    evaluation: RuleEvaluation,
    score: ScoreResult,
    shap: ShapResult,
    assessment: RiskAssessment,
) -> ResultRecord:
    """Assemble the immutable `analysis_results` snapshot from the deterministic-core outputs."""
    return ResultRecord(
        fraud_probability=score.fraud_probability,
        shap_values=shap.shap_values,
        top_features=[feature.model_dump(by_alias=True) for feature in shap.top_features],
        rule_hits=[rule_hit_json(hit) for hit in evaluation.hits],
        combined_score=assessment.combined_score,
        risk_band=assessment.risk_band,
        model_version=score.model_version_label,
    )


def build_sar_input(  # noqa: PLR0913 - assembles a SarInput from the five step outputs (DI; keyword-only).
    *,
    pipeline_input: PipelineInput,
    evaluation: RuleEvaluation,
    score: ScoreResult,
    shap: ShapResult,
    rag: RagResult,
    assessment: RiskAssessment,
) -> SarInput:
    """Assemble the PHI-free SarInput the drafter turns into a streamed SAR (plan §7.8)."""
    return SarInput(
        agency_id=pipeline_input.agency_id,
        transaction_id=pipeline_input.transaction_id,
        risk_band=assessment.risk_band,
        fraud_probability=score.fraud_probability,
        amount=pipeline_input.amount,
        currency=pipeline_input.currency,
        country=pipeline_input.country,
        channel=pipeline_input.channel,
        model_version=score.model_version_label,
        rules_version=evaluation.rules_version,
        rag_version=rag.rag_version,
        rule_hits=evaluation.hits,
        top_features=tuple(shap.top_features),
        citations=tuple(rag.citations),
        rag_context=rag.rag_context,
    )

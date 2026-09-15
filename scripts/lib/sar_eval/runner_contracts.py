"""Summary: Strict models and paired-fact invariants for SAR API evaluation runs.

Key classes:
- RetrievedRegulationFact:
- DurableEvaluationFacts:
- ToolEvidenceFact:
- ApiArmResult: one completed scenario-arm observation.
- ApiArmFailure:
- ApiArmReservation:
- ScenarioToolEvidence:
- ApiRunCheckpoint: resumable per-arm progress and reservations.
- ApiRunArtifact: a complete paired study artifact.

Key functions:
- paired_facts_equal: compare durable paired facts with bounded score jitter.

Notes:
- Pydantic models reject incomplete, unpaired, or over-budget artifacts.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    extra="forbid",
    alias_generator=to_camel,
    populate_by_name=True,
    protected_namespaces=(),
)
Arm = Literal["single_writer", "multi_agent"]
_ARMS: tuple[Arm, Arm] = ("single_writer", "multi_agent")
_SCENARIO_COUNT = 32
_PAIRED_ARM_COUNT = 2
_RAG_SCORE_ABS_TOLERANCE = 0.005


class _TerminalInvestigationError(RuntimeError):
    """A persisted investigation reached the terminal failed state."""


class RetrievedRegulationFact(BaseModel):
    """One exact PHI-free persisted regulatory chunk available to both drafting arms."""

    model_config = _MODEL_CONFIG

    chunk_id: str = Field(..., min_length=1, description="Stable corpus chunk identifier.")
    doc_id: str = Field(..., min_length=1, description="Stable source document identifier.")
    citation: str = Field(..., min_length=1, description="Exact regulatory citation.")
    title: str = Field(..., min_length=1, description="Title of the source provision.")
    source: str = Field(..., min_length=1, description="Publisher of the provision.")
    text: str = Field(..., min_length=1, description="Exact retrieved regulatory reference text.")
    score: float = Field(
        ..., allow_inf_nan=False, description="Persisted finite retrieval relevance score."
    )


class DurableEvaluationFacts(BaseModel):
    """Complete PHI-free persisted facts supplied to both candidate writers and the judge."""

    model_config = _MODEL_CONFIG

    fraud_probability: float = Field(..., ge=0, le=1, description="Persisted fraud probability.")
    risk_band: str = Field(..., min_length=1, description="Persisted resolved risk band.")
    rule_hits: tuple[dict[str, Any], ...] = Field(..., description="Persisted PHI-free rule hits.")
    top_features: tuple[dict[str, Any], ...] = Field(
        ..., description="Persisted SHAP feature names and values."
    )
    model_version: str = Field(..., min_length=1, description="Persisted scoring model version.")
    rules_version: str = Field(..., min_length=1, description="Persisted rules fingerprint.")
    rag_version: str = Field(..., min_length=1, description="Persisted corpus version.")
    retrieved_regulations: tuple[RetrievedRegulationFact, ...] = Field(
        ..., min_length=1, description="Exact persisted regulatory input chunks."
    )


class ToolEvidenceFact(BaseModel):
    """One completed PHI-masked tool result, detached from candidate identity."""

    model_config = _MODEL_CONFIG

    name: str = Field(..., min_length=1, description="Bounded tool name.")
    result: dict[str, Any] = Field(..., description="Persisted PHI-masked structured result.")


class ApiArmResult(BaseModel):
    """One completed scenario arm observed only through shipped API contracts."""

    model_config = _MODEL_CONFIG

    scenario_id: str = Field(..., min_length=1, description="Scenario key.")
    arm: Arm = Field(..., description="Explicit drafting workflow.")
    run_id: str = Field(..., min_length=1, description="Persisted investigation id.")
    narrative: str = Field(..., min_length=1, description="Persisted synthetic SAR narrative.")
    structured: dict[str, Any] = Field(..., description="Persisted structured SAR body.")
    facts: DurableEvaluationFacts = Field(..., description="Durable facts available to this arm.")
    citation_ids: tuple[str, ...] = Field(..., description="Grounded persisted citation ids.")
    writer_model_id: str = Field(
        ..., min_length=1, description="Model that persisted the SAR draft."
    )
    model_ids: tuple[str, ...] = Field(
        ..., min_length=1, description="Models observed in this arm."
    )
    prompt_versions: tuple[str, ...] = Field(
        ..., min_length=1, description="Prompt versions observed in this arm."
    )
    prompt_hashes: tuple[str, ...] = Field(
        ..., min_length=1, description="Exact prompt hashes observed in this arm."
    )
    graph_version: str | None = Field(default=None, description="Agent graph version when present.")
    cost_usd: Decimal = Field(..., ge=0, description="Persisted total drafting cost.")
    latency_ms: int = Field(
        ..., ge=0, description="Persisted investigation created-to-updated latency."
    )
    model_calls: int = Field(..., gt=0, description="Successful provider generations.")
    revision_count: int = Field(..., ge=0, description="Writer revisions.")
    completed_tool_evidence: tuple[ToolEvidenceFact, ...] = Field(
        default=(), description="Completed PHI-masked tool results observed in this arm."
    )

    @model_validator(mode="after")
    def _writer_is_observed(self) -> ApiArmResult:
        if self.writer_model_id not in self.model_ids:
            raise ValueError("writerModelId must be present in modelIds")
        if self.arm == "single_writer" and self.model_calls != 1:
            raise ValueError("single-writer modelCalls must equal one successful generation")
        if self.arm == "single_writer" and self.completed_tool_evidence:
            raise ValueError("single-writer results cannot contain agent tool evidence")
        return self


class ApiArmFailure(BaseModel):
    """One failed arm retained until an operator explicitly requests a retry."""

    model_config = _MODEL_CONFIG

    scenario_id: str = Field(..., min_length=1, description="Scenario key.")
    arm: Arm = Field(..., description="Failed workflow arm.")
    attempt_count: int = Field(..., ge=1, description="Explicit attempts made for this arm.")
    error_code: Literal["arm_failed"] = Field(..., description="Stable PHI-free failure code.")


class ApiArmReservation(BaseModel):
    """One conservative reservation persisted before an API attempt can incur spend."""

    model_config = _MODEL_CONFIG

    scenario_id: str = Field(..., min_length=1, description="Scenario key.")
    arm: Arm = Field(..., description="Workflow arm.")
    attempt: int = Field(..., ge=1, description="Idempotent attempt number.")
    amount_usd: Decimal = Field(..., gt=0, description="Conservative reserved USD amount.")


class ScenarioToolEvidence(BaseModel):
    """The completed tool-result union for exactly one paired scenario."""

    model_config = _MODEL_CONFIG

    scenario_id: str = Field(..., min_length=1, description="Scenario key.")
    evidence: tuple[ToolEvidenceFact, ...] = Field(
        ..., description="Deduplicated PHI-masked completed tool results."
    )


class ApiRunCheckpoint(BaseModel):
    """Resumable per-arm progress, including failures that require explicit retry authority."""

    model_config = _MODEL_CONFIG

    run_id: str = Field(..., min_length=1, description="Evaluation run id.")
    config_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="Protocol hash.")
    authorized_max_usd: Decimal = Field(..., gt=0, description="Explicit caller spend cap.")
    results: tuple[ApiArmResult, ...] = Field(
        default=(), max_length=64, description="Durably completed arm observations."
    )
    failures: tuple[ApiArmFailure, ...] = Field(
        default=(), max_length=64, description="Failed arms awaiting an explicit retry."
    )
    reservations: tuple[ApiArmReservation, ...] = Field(
        default=(), description="Monotonic pre-attempt spend reservations."
    )

    @model_validator(mode="after")
    def _unique_progress(self) -> ApiRunCheckpoint:
        result_keys = {(item.scenario_id, item.arm) for item in self.results}
        failure_keys = {(item.scenario_id, item.arm) for item in self.failures}
        if len(result_keys) != len(self.results) or len(failure_keys) != len(self.failures):
            raise ValueError("checkpoint arm keys must be unique")
        if result_keys & failure_keys:
            raise ValueError("an arm cannot be both completed and failed")
        reservation_keys = {
            (item.scenario_id, item.arm, item.attempt) for item in self.reservations
        }
        if len(reservation_keys) != len(self.reservations):
            raise ValueError("checkpoint attempt reservations must be unique")
        if sum((item.amount_usd for item in self.reservations), Decimal("0")) > (
            self.authorized_max_usd
        ):
            raise ValueError("checkpoint reservations exceed the authorized hard cap")
        if any(
            (failure.scenario_id, failure.arm, failure.attempt_count) not in reservation_keys
            for failure in self.failures
        ):
            raise ValueError("every failed arm must retain its attempt reservation")
        if any(
            not any(
                (reservation.scenario_id, reservation.arm) == (result.scenario_id, result.arm)
                for reservation in self.reservations
            )
            for result in self.results
        ):
            raise ValueError("every completed arm must retain an attempt reservation")
        _require_paired_facts(self.results)
        return self


class ApiRunArtifact(BaseModel):
    """All paired API observations for one protocol run."""

    model_config = _MODEL_CONFIG

    run_id: str = Field(..., min_length=1, description="Evaluation run id.")
    config_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="Protocol hash.")
    authorized_max_usd: Decimal = Field(..., gt=0, description="Explicit caller spend cap.")
    spent_usd: Decimal = Field(..., ge=0, description="Observed API drafting spend.")
    reserved_usd: Decimal = Field(..., ge=0, description="Cumulative conservative reservations.")
    reservations: tuple[ApiArmReservation, ...] = Field(
        ..., min_length=64, description="All monotonic per-attempt reservations."
    )
    results: tuple[ApiArmResult, ...] = Field(
        ..., min_length=64, max_length=64, description="Exactly two arms for each scenario."
    )
    scenario_tool_evidence: tuple[ScenarioToolEvidence, ...] = Field(
        ...,
        min_length=32,
        max_length=32,
        description="Completed tool-result unions isolated by paired scenario.",
    )

    @model_validator(mode="after")
    def _complete_and_bounded(self) -> ApiRunArtifact:
        keys = {(item.scenario_id, item.arm) for item in self.results}
        scenario_ids = {item.scenario_id for item in self.results}
        expected = {(scenario_id, arm) for scenario_id in scenario_ids for arm in _ARMS}
        if (
            len(scenario_ids) != _SCENARIO_COUNT
            or keys != expected
            or len(keys) != len(self.results)
        ):
            raise ValueError("API artifact must contain exactly two arms for each of 32 scenarios")
        if self.spent_usd > self.authorized_max_usd:
            raise ValueError("observed API spend exceeds the authorized hard cap")
        if self.reserved_usd > self.authorized_max_usd:
            raise ValueError("API reservations exceed the authorized hard cap")
        if self.spent_usd > self.reserved_usd:
            raise ValueError("observed API spend exceeds cumulative reservations")
        reservation_keys = {
            (item.scenario_id, item.arm, item.attempt) for item in self.reservations
        }
        if len(reservation_keys) != len(self.reservations):
            raise ValueError("API attempt reservations must be unique")
        if sum((item.amount_usd for item in self.reservations), Decimal("0")) != (
            self.reserved_usd
        ):
            raise ValueError("reservedUsd must equal the exact attempt reservation sum")
        if any(
            not any(
                (reservation.scenario_id, reservation.arm) == (result.scenario_id, result.arm)
                for reservation in self.reservations
            )
            for result in self.results
        ):
            raise ValueError("every completed arm must retain an attempt reservation")
        from lib.sar_eval.runner_facts import scenario_tool_evidence  # noqa: PLC0415

        if self.scenario_tool_evidence != scenario_tool_evidence(self.results):
            raise ValueError(
                "scenarioToolEvidence must equal each scenario's completed tool-result union"
            )
        _require_paired_facts(self.results)
        return self


def _require_paired_facts(results: tuple[ApiArmResult, ...]) -> None:
    by_scenario: dict[str, list[ApiArmResult]] = {}
    for result in results:
        by_scenario.setdefault(result.scenario_id, []).append(result)
    for paired in by_scenario.values():
        if len(paired) == _PAIRED_ARM_COUNT and not paired_facts_equal(
            paired[0].facts, paired[1].facts
        ):
            raise ValueError("paired workflow arms must expose identical durable evaluation facts")


def paired_facts_equal(
    left: DurableEvaluationFacts,
    right: DurableEvaluationFacts,
) -> bool:
    """Compare paired inputs while tolerating insignificant live-embedding score jitter."""
    if left.model_copy(update={"retrieved_regulations": ()}) != right.model_copy(
        update={"retrieved_regulations": ()}
    ):
        return False
    if len(left.retrieved_regulations) != len(right.retrieved_regulations):
        return False
    for left_regulation, right_regulation in zip(
        left.retrieved_regulations, right.retrieved_regulations, strict=True
    ):
        if left_regulation.model_copy(update={"score": 0.0}) != right_regulation.model_copy(
            update={"score": 0.0}
        ):
            return False
        if not math.isclose(
            left_regulation.score,
            right_regulation.score,
            rel_tol=0.0,
            abs_tol=_RAG_SCORE_ABS_TOLERANCE,
        ):
            return False
    return True

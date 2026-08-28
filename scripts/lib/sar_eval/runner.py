"""Summary: Real-API paired-arm harness for the synthetic SAR evaluation.
It ingests each scenario through the public transaction endpoint, starts both explicit
workflow modes with deterministic idempotency keys, polls the durable snapshot, and reads
the existing alert-detail contract for the persisted SAR and execution provenance.

Key classes:
- RetrievedRegulationFact: one exact persisted PHI-free RAG input chunk.
- ToolEvidenceFact: one completed, PHI-masked tool result shared with the blind judge.
- ScenarioToolEvidence: the tool-result union isolated to one paired scenario.
- DurableEvaluationFacts: PHI-free persisted facts available to both drafting arms.
- ApiArmResult: one completed scenario-arm observation.
- ApiArmReservation: one conservative pre-POST spend reservation for an attempt.
- ApiArmFailure: one explicitly retryable failed arm checkpoint.
- ApiRunCheckpoint: resumable per-arm progress with original completed observations.
- ApiRunArtifact: all 64 real-API observations plus the enforced spend cap.

Key functions:
- run_api_stage: execute the paired study against an authenticated live API.
- write_api_runs: atomically serialize completed API observations.
- load_api_runs: strictly parse a completed API-stage artifact.

Notes:
- `modelCalls` fails closed unless the API reports each multi-agent execution's
  `modelCallCount`; execution rows and tool calls are not silently used as proxies.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic.alias_generators import to_camel

from lib.sar_eval.config import SarEvalConfig
from lib.sar_eval.scenarios import (
    SarEvalScenario,
    ScenarioArtifact,
    SyntheticTransaction,
    validate_scenario_binding,
)

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    extra="forbid",
    alias_generator=to_camel,
    populate_by_name=True,
    protected_namespaces=(),
)
Arm = Literal["single_writer", "multi_agent"]
_ARMS: tuple[Arm, Arm] = ("single_writer", "multi_agent")
_TERMINAL = frozenset({"completed", "failed"})
_SCENARIO_COUNT = 32
_CREATED = 201
_CONFLICT = 409
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PAIRED_ARM_COUNT = 2


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
        if self.scenario_tool_evidence != _scenario_tool_evidence(self.results):
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
        if len(paired) == _PAIRED_ARM_COUNT and paired[0].facts != paired[1].facts:
            raise ValueError("paired workflow arms must expose identical durable evaluation facts")


def _atomic_write(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    content = (
        json.dumps(value.model_dump(mode="json", by_alias=True), indent=2, sort_keys=True) + "\n"
    )
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _body(response: httpx.Response, expected: int) -> dict[str, Any]:
    if response.status_code != expected:
        raise RuntimeError(f"API request failed with status {response.status_code}")
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("API response must be an object")
    return value


def _matches_visible_transaction(row: Mapping[str, Any], transaction: SyntheticTransaction) -> bool:
    try:
        occurred_at = datetime.fromisoformat(str(row["occurredAt"]).replace("Z", "+00:00"))
        amount = Decimal(str(row["amount"]))
    except (KeyError, ArithmeticError, TypeError, ValueError):
        return False
    expected_time = transaction.occurred_at
    return (
        row.get("externalId") == transaction.external_id
        and amount == transaction.amount
        and row.get("currency") == transaction.currency
        and occurred_at == expected_time
        and row.get("channel") == transaction.channel
        and row.get("country") == transaction.country
    )


def _ingest(client: httpx.Client, scenario: SarEvalScenario) -> str:
    subject_id: str | None = None
    for transaction in scenario.transactions:
        response = client.post(
            "/api/v1/transactions",
            json=transaction.model_dump(mode="json", by_alias=True),
        )
        if response.status_code == _CREATED:
            body = _body(response, _CREATED)
        elif response.status_code == _CONFLICT:
            listing = _body(
                client.get(
                    "/api/v1/transactions",
                    params={"search": transaction.external_id, "limit": 100},
                ),
                200,
            )
            rows = listing.get("transactions")
            matches = (
                [
                    row
                    for row in rows
                    if isinstance(row, dict) and row.get("externalId") == transaction.external_id
                ]
                if isinstance(rows, list)
                else []
            )
            if len(matches) != 1:
                raise RuntimeError("duplicate synthetic transaction could not be resolved exactly")
            body = matches[0]
            if not _matches_visible_transaction(body, transaction):
                raise RuntimeError(
                    "duplicate synthetic transaction differs from the current scenario"
                )
        else:
            raise RuntimeError(f"transaction ingest failed with status {response.status_code}")
        if transaction.external_id == scenario.subject_external_id:
            subject_id = str(body["transactionId"])
    if subject_id is None:
        raise RuntimeError("scenario subject transaction was not ingested")
    return subject_id


def _poll(  # noqa: PLR0913 -- injectable time functions keep polling deterministic.
    client: httpx.Client,
    run_id: str,
    *,
    timeout_s: float,
    poll_interval_s: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    deadline = clock() + timeout_s
    while clock() < deadline:
        snapshot = _body(client.get(f"/api/v1/investigations/{run_id}"), 200)
        if snapshot.get("runId") not in (None, run_id):
            raise RuntimeError("investigation snapshot run id does not match the requested run")
        status = snapshot.get("status")
        if status in _TERMINAL:
            if status != "completed":
                raise _TerminalInvestigationError(
                    "investigation failed before producing an evaluation artifact"
                )
            return snapshot
        sleep(poll_interval_s)
    raise RuntimeError("investigation did not finish before the configured timeout")


def _unique_strings(values: list[object]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if isinstance(value, str) and value))


def _multi_model_calls(executions: object) -> int:
    if not isinstance(executions, list) or not executions:
        raise RuntimeError("multi-agent result has no persisted execution trace")
    total = 0
    for execution in executions:
        if not isinstance(execution, Mapping):
            raise RuntimeError("agent execution trace is malformed")
        count = execution.get("modelCallCount")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise RuntimeError(
                "multi-agent execution lacks non-negative modelCallCount; "
                "modelCalls cannot be proxied"
            )
        total += count
    if total <= 0:
        raise RuntimeError("multi-agent trace contains no successful model generations")
    return total


def _execution_cost(executions: list[object]) -> Decimal:
    total = Decimal("0")
    for execution in executions:
        if not isinstance(execution, Mapping):
            raise RuntimeError("agent execution trace is malformed")
        try:
            cost = Decimal(str(execution["costUsd"]))
        except (KeyError, ArithmeticError) as exc:
            raise RuntimeError("agent execution lacks an exact persisted costUsd") from exc
        if not cost.is_finite() or cost < 0:
            raise RuntimeError("agent execution costUsd must be finite and non-negative")
        total += cost
    return total


def _completed_tool_evidence(executions: list[object]) -> tuple[ToolEvidenceFact, ...]:
    evidence: list[ToolEvidenceFact] = []
    for execution in executions:
        if not isinstance(execution, Mapping):
            raise RuntimeError("agent execution trace is malformed")
        calls = execution.get("toolCalls", [])
        if not isinstance(calls, list):
            raise RuntimeError("agent execution toolCalls must be a list")
        for call in calls:
            if not isinstance(call, Mapping):
                raise RuntimeError("agent execution tool call is malformed")
            if call.get("status") != "completed":
                continue
            name = call.get("name")
            result = call.get("result")
            if not isinstance(name, str) or not name or not isinstance(result, Mapping):
                raise RuntimeError("completed tool call lacks a structured PHI-masked result")
            evidence.append(ToolEvidenceFact(name=name, result=dict(result)))
    return _dedupe_tool_evidence(evidence)


def _dedupe_tool_evidence(
    evidence: list[ToolEvidenceFact],
) -> tuple[ToolEvidenceFact, ...]:
    unique: dict[str, ToolEvidenceFact] = {}
    for item in evidence:
        canonical = json.dumps(item.model_dump(mode="json", by_alias=True), sort_keys=True)
        unique.setdefault(canonical, item)
    return tuple(unique[key] for key in sorted(unique))


def _union_tool_evidence(results: tuple[ApiArmResult, ...]) -> tuple[ToolEvidenceFact, ...]:
    return _dedupe_tool_evidence(
        [item for result in results for item in result.completed_tool_evidence]
    )


def _scenario_tool_evidence(
    results: tuple[ApiArmResult, ...],
) -> tuple[ScenarioToolEvidence, ...]:
    scenario_ids = sorted({item.scenario_id for item in results})
    return tuple(
        ScenarioToolEvidence(
            scenario_id=scenario_id,
            evidence=_union_tool_evidence(
                tuple(item for item in results if item.scenario_id == scenario_id)
            ),
        )
        for scenario_id in scenario_ids
    )


def _durable_facts(snapshot: dict[str, Any]) -> DurableEvaluationFacts:
    required_text = ("riskBand", "modelVersion", "rulesVersion", "ragVersion")
    if any(
        not isinstance(snapshot.get(field), str) or not snapshot[field] for field in required_text
    ):
        raise RuntimeError("completed snapshot lacks required durable evaluation provenance")
    probability = snapshot.get("fraudProbability")
    if not isinstance(probability, int | float) or isinstance(probability, bool):
        raise RuntimeError("completed snapshot lacks a numeric fraudProbability")
    collections: dict[str, tuple[dict[str, Any], ...]] = {}
    for field in ("ruleHits", "topFeatures"):
        raw = snapshot.get(field)
        if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
            raise RuntimeError(f"completed snapshot {field} must be a list of objects")
        collections[field] = tuple(dict(item) for item in raw)
    raw_regulations = snapshot.get("retrievedRegulations")
    if not isinstance(raw_regulations, list) or not raw_regulations:
        raise RuntimeError("completed snapshot lacks persisted retrievedRegulations")
    try:
        regulations = tuple(
            RetrievedRegulationFact.model_validate(item) for item in raw_regulations
        )
    except ValidationError as exc:
        raise RuntimeError("completed snapshot retrievedRegulations is malformed") from exc
    return DurableEvaluationFacts(
        fraud_probability=float(probability),
        risk_band=str(snapshot["riskBand"]),
        rule_hits=collections["ruleHits"],
        top_features=collections["topFeatures"],
        model_version=str(snapshot["modelVersion"]),
        rules_version=str(snapshot["rulesVersion"]),
        rag_version=str(snapshot["ragVersion"]),
        retrieved_regulations=regulations,
    )


def _arm_result(
    scenario: SarEvalScenario,
    arm: Arm,
    snapshot: dict[str, Any],
    detail: dict[str, Any],
    latency_ms: int,
) -> ApiArmResult:
    if snapshot.get("workflowMode") != arm:
        raise RuntimeError("completed snapshot workflowMode does not match the requested arm")
    if detail.get("workflowMode") != arm:
        raise RuntimeError("alert detail workflowMode does not match the requested arm")
    draft = detail.get("sarDraft")
    if not isinstance(draft, dict) or draft.get("status") != "draft":
        raise RuntimeError("completed investigation has no persisted draft SAR")
    if draft.get("workflow") != arm:
        raise RuntimeError("persisted sarDraft workflow does not match the requested arm")
    executions = detail.get("agentExecutions", [])
    execution_rows = executions if isinstance(executions, list) else []
    writer_model_id = draft.get("modelId")
    if not isinstance(writer_model_id, str) or not writer_model_id:
        raise RuntimeError("persisted SAR draft has no writer model id")
    model_ids = [
        writer_model_id,
        *(row.get("modelId") for row in execution_rows if isinstance(row, dict)),
    ]
    prompt_versions = [
        draft.get("promptVersion"),
        *(row.get("promptVersion") for row in execution_rows if isinstance(row, dict)),
    ]
    prompt_hashes = [
        draft.get("promptHash"),
        *(row.get("promptHash") for row in execution_rows if isinstance(row, dict)),
    ]
    citations = draft.get("citations", [])
    citation_ids = (
        tuple(
            str(item["citation"])
            for item in citations
            if isinstance(item, dict) and isinstance(item.get("citation"), str)
        )
        if isinstance(citations, list)
        else ()
    )
    draft_cost = Decimal(str(draft["costUsd"]))
    if arm == "single_writer":
        if execution_rows:
            raise RuntimeError("single-writer result unexpectedly contains agent executions")
        model_calls = 1
    else:
        model_calls = _multi_model_calls(execution_rows)
        if _execution_cost(execution_rows) != draft_cost:
            raise RuntimeError("multi-agent sarDraft costUsd must equal persisted execution costs")
    draft_revision = int(draft.get("revisionCount", 0))
    if int(detail.get("revisionCount", 0)) != draft_revision:
        raise RuntimeError("alert and sarDraft revision counts do not agree")
    return ApiArmResult(
        scenario_id=scenario.scenario_id,
        arm=arm,
        run_id=str(snapshot["runId"]),
        narrative=str(draft["content"]),
        structured=dict(draft.get("structured") or {}),
        facts=_durable_facts(snapshot),
        citation_ids=citation_ids,
        writer_model_id=writer_model_id,
        model_ids=_unique_strings(model_ids),
        prompt_versions=_unique_strings(prompt_versions),
        prompt_hashes=_unique_strings(prompt_hashes),
        graph_version=(str(detail["graphVersion"]) if detail.get("graphVersion") else None),
        cost_usd=draft_cost,
        latency_ms=latency_ms,
        model_calls=model_calls,
        revision_count=draft_revision,
        completed_tool_evidence=_completed_tool_evidence(execution_rows),
    )


def _idempotency_key(eval_run_id: str, scenario_id: str, arm: Arm, attempt: int) -> str:
    return hashlib.sha256(
        f"{eval_run_id}:{scenario_id}:{arm}:attempt:{attempt}".encode()
    ).hexdigest()


def _persisted_latency_ms(snapshot: dict[str, Any]) -> int:
    try:
        created = datetime.fromisoformat(str(snapshot["createdAt"]).replace("Z", "+00:00"))
        updated = datetime.fromisoformat(str(snapshot["updatedAt"]).replace("Z", "+00:00"))
        elapsed_ms = round((updated - created).total_seconds() * 1000)
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("completed snapshot lacks valid persisted latency timestamps") from exc
    if created.tzinfo is None or updated.tzinfo is None or elapsed_ms < 0:
        raise RuntimeError("completed snapshot has invalid persisted latency timestamps")
    return elapsed_ms


def _arm_order(seed: int, scenario_id: str) -> tuple[Arm, Arm]:
    digest = hashlib.sha256(f"{seed}:{scenario_id}:arm-order".encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    return _ARMS if rng.randrange(2) == 0 else (_ARMS[1], _ARMS[0])


def _checkpoint_path(config: SarEvalConfig, run_id: str) -> Path:
    return _REPO_ROOT / config.paths.output_dir / run_id / "runs.checkpoint.json"


def _load_checkpoint(
    path: Path,
    scenarios: ScenarioArtifact,
    config: SarEvalConfig,
    max_usd: Decimal,
) -> ApiRunCheckpoint:
    if not path.exists():
        return ApiRunCheckpoint(
            run_id=scenarios.run_id,
            config_sha256=scenarios.config_sha256,
            authorized_max_usd=max_usd,
        )
    checkpoint = ApiRunCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))
    if checkpoint.run_id != scenarios.run_id:
        raise ValueError("API checkpoint run id does not match the requested evaluation run")
    validate_scenario_binding(
        scenarios,
        expected_run_id=checkpoint.run_id,
        config=config,
    )
    if checkpoint.config_sha256 != scenarios.config_sha256:
        raise ValueError("API checkpoint config SHA does not match the scenario artifact")
    expected_reservation = Decimal(str(config.api.max_cost_usd_per_run))
    if any(item.amount_usd != expected_reservation for item in checkpoint.reservations):
        raise ValueError("API checkpoint reservation does not match the loaded protocol")
    if max_usd < checkpoint.authorized_max_usd:
        raise ValueError("resumed API run cannot lower its previously authorized USD cap")
    if max_usd > checkpoint.authorized_max_usd:
        return checkpoint.model_copy(update={"authorized_max_usd": max_usd})
    return checkpoint


def _write_checkpoint(path: Path, checkpoint: ApiRunCheckpoint) -> None:
    _atomic_write(path, checkpoint)


def run_api_stage(  # noqa: PLR0913 -- explicit stage dependencies aid deterministic tests.
    scenarios: ScenarioArtifact,
    config: SarEvalConfig,
    *,
    client: httpx.Client,
    max_usd: Decimal,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    checkpoint_path: Path | None = None,
    retry_failed: bool = False,
) -> ApiRunArtifact:
    """Execute randomized paired arms with resumable checkpoints and an explicit total cap."""
    validate_scenario_binding(
        scenarios,
        expected_run_id=scenarios.run_id,
        config=config,
    )
    reserve = Decimal(str(config.api.max_cost_usd_per_run))
    target = checkpoint_path or _checkpoint_path(config, scenarios.run_id)
    checkpoint = _load_checkpoint(target, scenarios, config, max_usd)
    results = list(checkpoint.results)
    failure_map = {(item.scenario_id, item.arm): item for item in checkpoint.failures}
    reservations = list(checkpoint.reservations)
    reservation_keys = {(item.scenario_id, item.arm, item.attempt) for item in reservations}
    result_keys = {(item.scenario_id, item.arm) for item in results}
    spent = sum((item.cost_usd for item in results), Decimal("0"))
    reserved = sum((item.amount_usd for item in reservations), Decimal("0"))
    for scenario in scenarios.scenarios:
        order = _arm_order(config.seed, scenario.scenario_id)
        if all((scenario.scenario_id, arm) in result_keys for arm in order):
            continue
        if not retry_failed and any(
            (scenario.scenario_id, arm) in failure_map
            for arm in order
            if (scenario.scenario_id, arm) not in result_keys
        ):
            raise RuntimeError("checkpoint contains a failed arm; explicit retry is required")
        transaction_id = _ingest(client, scenario)
        for arm in order:
            key = (scenario.scenario_id, arm)
            if key in result_keys:
                continue
            prior_failure = failure_map.get(key)
            if prior_failure is not None and not retry_failed:
                raise RuntimeError("checkpoint contains a failed arm; explicit retry is required")
            attempt = prior_failure.attempt_count + 1 if prior_failure else 1
            reservation_key = (scenario.scenario_id, arm, attempt)
            if reservation_key not in reservation_keys:
                if reserved + reserve > max_usd:
                    raise RuntimeError("next API arm could exceed the authorized hard USD cap")
                reservations.append(
                    ApiArmReservation(
                        scenario_id=scenario.scenario_id,
                        arm=arm,
                        attempt=attempt,
                        amount_usd=reserve,
                    )
                )
                reservation_keys.add(reservation_key)
                reserved += reserve
                _write_checkpoint(
                    target,
                    ApiRunCheckpoint(
                        run_id=scenarios.run_id,
                        config_sha256=scenarios.config_sha256,
                        authorized_max_usd=max_usd,
                        results=tuple(results),
                        failures=tuple(failure_map.values()),
                        reservations=tuple(reservations),
                    ),
                )
            terminal_observed = False
            try:
                response = client.post(
                    "/api/v1/investigations",
                    json={
                        "transactionId": transaction_id,
                        "workflowMode": arm,
                    },
                    headers={
                        "Idempotency-Key": _idempotency_key(
                            scenarios.run_id, scenario.scenario_id, arm, attempt
                        )
                    },
                )
                run_id = str(_body(response, 202)["runId"])
                snapshot = _poll(
                    client,
                    run_id,
                    timeout_s=config.api.run_timeout_s,
                    poll_interval_s=config.api.poll_interval_s,
                    clock=clock,
                    sleep=sleep,
                )
                terminal_observed = True
                latency_ms = _persisted_latency_ms(snapshot)
                alert_id = snapshot.get("alertId")
                if not isinstance(alert_id, str) or not alert_id:
                    raise RuntimeError("evaluation scenario did not raise an alert")
                detail = _body(client.get(f"/api/v1/alerts/{alert_id}"), 200)
                observed = _arm_result(scenario, arm, snapshot, detail, latency_ms)
                if observed.cost_usd > reserve:
                    raise RuntimeError("one API arm exceeded the configured per-run cost cap")
                paired = [item for item in results if item.scenario_id == scenario.scenario_id]
                if paired and paired[0].facts != observed.facts:
                    raise RuntimeError("paired snapshots expose different durable evaluation facts")
            except Exception as exc:
                if terminal_observed or isinstance(exc, _TerminalInvestigationError):
                    failure_map[key] = ApiArmFailure(
                        scenario_id=scenario.scenario_id,
                        arm=arm,
                        attempt_count=attempt,
                        error_code="arm_failed",
                    )
                _write_checkpoint(
                    target,
                    ApiRunCheckpoint(
                        run_id=scenarios.run_id,
                        config_sha256=scenarios.config_sha256,
                        authorized_max_usd=max_usd,
                        results=tuple(results),
                        failures=tuple(failure_map.values()),
                        reservations=tuple(reservations),
                    ),
                )
                raise RuntimeError("API evaluation arm failed; checkpoint retained") from exc
            spent += observed.cost_usd
            results.append(observed)
            result_keys.add(key)
            failure_map.pop(key, None)
            _write_checkpoint(
                target,
                ApiRunCheckpoint(
                    run_id=scenarios.run_id,
                    config_sha256=scenarios.config_sha256,
                    authorized_max_usd=max_usd,
                    results=tuple(results),
                    failures=tuple(failure_map.values()),
                    reservations=tuple(reservations),
                ),
            )
    return ApiRunArtifact(
        run_id=scenarios.run_id,
        config_sha256=scenarios.config_sha256,
        authorized_max_usd=max_usd,
        spent_usd=spent,
        reserved_usd=reserved,
        reservations=tuple(reservations),
        results=tuple(results),
        scenario_tool_evidence=_scenario_tool_evidence(tuple(results)),
    )


def write_api_runs(path: Path, artifact: ApiRunArtifact) -> None:
    """Atomically serialize completed API observations."""
    if path.parent.name != artifact.run_id:
        raise ValueError("API artifact path must be nested under its exact run id")
    _atomic_write(path, artifact)


def load_api_runs(path: Path) -> ApiRunArtifact:
    """Strictly parse completed API observations."""
    artifact = ApiRunArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    if artifact.run_id != path.parent.name:
        raise ValueError("API artifact run id does not match the requested CLI run id")
    return artifact

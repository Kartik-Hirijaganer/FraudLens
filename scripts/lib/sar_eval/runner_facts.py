"""Summary: Durable-fact extraction, provenance accounting, and arm helpers for SAR evaluation.

Key classes:
- (none)

Key functions:
- multi_model_calls:
- scenario_tool_evidence: derive paired tool-evidence unions.
- arm_result: construct one strict API-arm observation.
- idempotency_key: derive a deterministic attempt key.
- persisted_latency_ms: derive latency from persisted timestamps.
- arm_order:

Notes:
- Multi-agent model calls come only from durable execution counts.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from lib.sar_eval.runner_contracts import (
    ApiArmResult,
    Arm,
    DurableEvaluationFacts,
    RetrievedRegulationFact,
    ScenarioToolEvidence,
    ToolEvidenceFact,
)
from lib.sar_eval.scenarios import SarEvalScenario

_ARMS: tuple[Arm, Arm] = ("single_writer", "multi_agent")


def _unique_strings(values: list[object]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if isinstance(value, str) and value))


def multi_model_calls(executions: object) -> int:
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


def scenario_tool_evidence(
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


def arm_result(
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
        model_calls = multi_model_calls(execution_rows)
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


def idempotency_key(eval_run_id: str, scenario_id: str, arm: Arm, attempt: int) -> str:
    return hashlib.sha256(
        f"{eval_run_id}:{scenario_id}:{arm}:attempt:{attempt}".encode()
    ).hexdigest()


def persisted_latency_ms(snapshot: dict[str, Any]) -> int:
    try:
        created = datetime.fromisoformat(str(snapshot["createdAt"]).replace("Z", "+00:00"))
        updated = datetime.fromisoformat(str(snapshot["updatedAt"]).replace("Z", "+00:00"))
        elapsed_ms = round((updated - created).total_seconds() * 1000)
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("completed snapshot lacks valid persisted latency timestamps") from exc
    if created.tzinfo is None or updated.tzinfo is None or elapsed_ms < 0:
        raise RuntimeError("completed snapshot has invalid persisted latency timestamps")
    return elapsed_ms


def arm_order(seed: int, scenario_id: str) -> tuple[Arm, Arm]:
    digest = hashlib.sha256(f"{seed}:{scenario_id}:arm-order".encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    return _ARMS if rng.randrange(2) == 0 else (_ARMS[1], _ARMS[0])

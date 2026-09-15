"""Behavioral tests for authenticated API orchestration and blind structured judging."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, cast

import httpx

from fraudlens_llm import LlmResult, LlmUsage
from lib.sar_eval.config import DEFAULT_SAR_EVAL_CONFIG, load_sar_eval_config
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
_SCORING_MODEL = load_sar_eval_config().calibration.model_version


def _facts() -> DurableEvaluationFacts:
    return DurableEvaluationFacts(
        fraud_probability=0.91,
        risk_band="high",
        rule_hits=({"code": "structuring", "reason": "Three sub-threshold synthetic deposits."},),
        top_features=({"feature": "amount_log", "value": 9.1, "shapValue": 0.72},),
        model_version=_SCORING_MODEL,
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


def _snapshot(arm: Arm, *, run_id: str = "run-test") -> dict[str, Any]:
    facts = _facts().model_dump(mode="json", by_alias=True)
    return {
        "runId": run_id,
        "status": "completed",
        "alertId": f"alert-{run_id}",
        "workflowMode": arm,
        **facts,
        "createdAt": "2026-01-15T12:00:00+00:00",
        "updatedAt": "2026-01-15T12:00:01.250000+00:00",
    }


def _tool_evidence(scenario_id: str = "tool-fixture") -> ToolEvidenceFact:
    return ToolEvidenceFact(
        name="transaction_history",
        result={
            "historicalSyntheticCount": 3,
            "scenarioKey": scenario_id,
            "transactions": [{"amount": "9200", "currency": "USD", "channel": "cash_deposit"}],
        },
    )


def _detail(arm: Arm) -> dict[str, Any]:
    executions = (
        [
            {
                "modelId": "openrouter/anthropic/claude-sonnet-4.6",
                "promptVersion": "reviewer@1.0.0",
                "promptHash": "b" * 64,
                "modelCallCount": 2,
                "costUsd": "0.0006",
                "result": {"agentGeneratedConclusion": "do not treat as ground truth"},
                "toolCalls": [
                    {
                        "callId": "call-history",
                        "name": "transaction_history",
                        "arguments": {},
                        "status": "completed",
                        "errorCode": None,
                        "result": _tool_evidence().result,
                    },
                    {
                        "callId": "call-refused",
                        "name": "alert_history",
                        "arguments": {},
                        "status": "refused",
                        "errorCode": "tool_refused",
                        "result": {"mustNotAppear": True},
                    },
                ],
            },
            {
                "modelId": _WRITER,
                "promptVersion": "writer@1.0.0",
                "promptHash": _HASH,
                "modelCallCount": 1,
                "costUsd": "0.0004",
            },
        ]
        if arm == "multi_agent"
        else []
    )
    return {
        "workflowMode": arm,
        "graphVersion": "agents-v1" if executions else None,
        "agentExecutions": executions,
        "revisionCount": 0,
        "sarDraft": {
            "status": "draft",
            "content": f"Synthetic {arm} narrative.",
            "structured": {"synthetic": True},
            "citations": [{"citation": "31 U.S.C. 5318(g)"}],
            "modelId": _WRITER,
            "promptVersion": "sar_writer@1.0.0",
            "promptHash": _HASH,
            "workflow": arm,
            "costUsd": "0.001",
            "revisionCount": 0,
        },
    }


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.01
        return self.value


class _Api:
    def __init__(
        self,
        *,
        fail_snapshot_for_run: int | None = None,
        crash_after_post_for_run: int | None = None,
    ) -> None:
        self.run_index = 0
        self.fail_snapshot_for_run = fail_snapshot_for_run
        self.crash_after_post_for_run = crash_after_post_for_run
        self.runs: dict[str, str] = {}
        self.workflows: list[str] = []
        self.idempotency_keys: list[str] = []
        self.auth_headers: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.auth_headers.append(request.headers.get("Authorization", ""))
        if request.method == "POST" and request.url.path == "/api/v1/transactions":
            body = json.loads(request.content)
            return httpx.Response(201, json={"transactionId": body["externalId"]})
        if request.method == "POST" and request.url.path == "/api/v1/investigations":
            body = json.loads(request.content)
            self.run_index += 1
            run_id = f"run-{self.run_index}"
            self.runs[run_id] = body["workflowMode"]
            self.workflows.append(body["workflowMode"])
            self.idempotency_keys.append(request.headers["Idempotency-Key"])
            assert body["transactionId"].startswith("sar-eval-")
            assert body["modelOverride"] == _SCORING_MODEL
            if self.crash_after_post_for_run == self.run_index:
                self.crash_after_post_for_run = None
                raise SystemExit("simulated process crash")
            return httpx.Response(202, json={"runId": run_id})
        if request.method == "GET" and request.url.path.startswith("/api/v1/investigations/"):
            run_id = request.url.path.rsplit("/", maxsplit=1)[-1]
            if self.fail_snapshot_for_run == int(run_id.removeprefix("run-")):
                self.fail_snapshot_for_run = None
                return httpx.Response(200, json={"status": "failed"})
            return httpx.Response(200, json=_snapshot(cast(Arm, self.runs[run_id]), run_id=run_id))
        if request.method == "GET" and request.url.path.startswith("/api/v1/alerts/"):
            run_id = request.url.path.removeprefix("/api/v1/alerts/alert-")
            arm = self.runs[run_id]
            return httpx.Response(200, json=_detail(cast(Arm, arm)))
        return httpx.Response(404)


class _Judge:
    def __init__(self, *, hallucinated_span: bool = False) -> None:
        self.calls: list[tuple[object, dict[str, Any]]] = []
        self.hallucinated_span = hallucinated_span

    async def generate(self, messages: object, **kwargs: Any) -> LlmResult:
        self.calls.append((messages, kwargs))
        response = {
            "candidates": [
                {
                    "candidate": label,
                    "unsupportedClaims": (
                        [
                            {
                                "quotedSpan": "not-in-candidate-unsupported",
                                "reason": "Synthetic invalid quote.",
                            }
                        ]
                        if self.hallucinated_span
                        else []
                    ),
                    "elements": [
                        {
                            "element": element,
                            "present": True,
                            "quotedSpan": (
                                f"not-in-candidate-{element}"
                                if self.hallucinated_span
                                else "Synthetic"
                            ),
                        }
                        for element in ("who", "what", "when", "where", "why")
                    ],
                }
                for label in ("A", "B")
            ]
        }
        return LlmResult.model_construct(
            safe_text=json.dumps(response),
            model="openrouter/anthropic/claude-opus-4.6",
            provider="openrouter",
            usage=LlmUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            tool_calls=(),
            guardrail=cast(Any, None),
        )


def _scenarios() -> ScenarioArtifact:
    return generate_scenarios(load_sar_eval_config(), DEFAULT_SAR_EVAL_CONFIG.read_bytes())


def _api_runs(scenarios: ScenarioArtifact) -> ApiRunArtifact:
    arms: tuple[Arm, Arm] = ("single_writer", "multi_agent")
    results = tuple(
        ApiArmResult(
            scenario_id=scenario.scenario_id,
            arm=arm,
            run_id=f"run-{scenario.scenario_id}-{arm}",
            narrative="Synthetic paired-candidate narrative.",
            structured={"synthetic": True},
            facts=_facts(),
            citation_ids=scenario.expected_citation_ids,
            writer_model_id=_WRITER,
            model_ids=(
                (_WRITER, "openrouter/anthropic/claude-sonnet-4.6")
                if arm == "multi_agent"
                else (_WRITER,)
            ),
            prompt_versions=("writer@1.0.0",),
            prompt_hashes=(_HASH,),
            graph_version="agents-v1" if arm == "multi_agent" else None,
            cost_usd=Decimal("0.001"),
            latency_ms=100,
            model_calls=3 if arm == "multi_agent" else 1,
            revision_count=0,
            completed_tool_evidence=(
                (_tool_evidence(scenario.scenario_id),) if arm == "multi_agent" else ()
            ),
        )
        for scenario in scenarios.scenarios
        for arm in arms
    )
    return ApiRunArtifact(
        run_id=scenarios.run_id,
        config_sha256=scenarios.config_sha256,
        authorized_max_usd=Decimal("10"),
        spent_usd=Decimal("0.064"),
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
        results=results,
        scenario_tool_evidence=tuple(
            ScenarioToolEvidence(
                scenario_id=scenario.scenario_id,
                evidence=(_tool_evidence(scenario.scenario_id),),
            )
            for scenario in sorted(scenarios.scenarios, key=lambda item: item.scenario_id)
        ),
    )

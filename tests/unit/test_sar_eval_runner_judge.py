"""Behavioral tests for authenticated API orchestration and blind structured judging."""

from __future__ import annotations

import json
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from fraudlens_backend.settings import find_config_dir
from fraudlens_llm import LlmMessage, LlmResult, LlmUsage, load_catalog
from lib.sar_eval.config import DEFAULT_SAR_EVAL_CONFIG, load_sar_eval_config
from lib.sar_eval.judge import (
    CandidateScore,
    ElementName,
    ElementScore,
    JudgeClient,
    JudgePromptTemplate,
    JudgeResponse,
    JudgmentArtifact,
    load_judgments,
    model_family,
    run_judge_stage,
    write_judgments,
)
from lib.sar_eval.runner import (
    ApiArmReservation,
    ApiArmResult,
    ApiRunArtifact,
    ApiRunCheckpoint,
    Arm,
    DurableEvaluationFacts,
    RetrievedRegulationFact,
    ScenarioToolEvidence,
    ToolEvidenceFact,
    _arm_result,
    _body,
    _idempotency_key,
    _ingest,
    _multi_model_calls,
    _persisted_latency_ms,
    _poll,
    load_api_runs,
    run_api_stage,
    write_api_runs,
)
from lib.sar_eval.scenarios import ScenarioArtifact, generate_scenarios

_HASH = "a" * 64
_WRITER = "openrouter/openai/gpt-5-mini"


def _facts() -> DurableEvaluationFacts:
    return DurableEvaluationFacts(
        fraud_probability=0.91,
        risk_band="high",
        rule_hits=({"code": "structuring", "reason": "Three sub-threshold synthetic deposits."},),
        top_features=({"feature": "amount_log", "value": 9.1, "shapValue": 0.72},),
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
                    "unsupportedClaims": [],
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


def test_real_api_stage_uses_bearer_workflow_mode_idempotency_and_persisted_counts(
    tmp_path: Path,
) -> None:
    api = _Api()
    clock = _Clock()
    with httpx.Client(
        base_url="https://fraudlens.invalid",
        headers={"Authorization": "Bearer synthetic-test-token"},
        transport=httpx.MockTransport(api),
    ) as client:
        artifact = run_api_stage(
            _scenarios(),
            load_sar_eval_config(),
            client=client,
            max_usd=Decimal("10"),
            clock=clock,
            sleep=lambda _seconds: None,
            checkpoint_path=tmp_path / "runs.checkpoint.json",
        )

    assert len(artifact.results) == 64
    assert api.workflows.count("single_writer") == 32
    assert api.workflows.count("multi_agent") == 32
    assert len(set(api.idempotency_keys)) == 64
    paired_orders = list(zip(api.workflows[::2], api.workflows[1::2], strict=True))
    assert all(set(order) == {"single_writer", "multi_agent"} for order in paired_orders)
    assert len(set(paired_orders)) == 2
    assert all(value == "Bearer synthetic-test-token" for value in api.auth_headers)
    assert {item.model_calls for item in artifact.results if item.arm == "single_writer"} == {1}
    assert {item.model_calls for item in artifact.results if item.arm == "multi_agent"} == {3}
    assert all(item.writer_model_id in item.model_ids for item in artifact.results)
    assert artifact.reserved_usd == Decimal("6.4")
    assert {item.latency_ms for item in artifact.results} == {1250}
    for scenario_id in {item.scenario_id for item in artifact.results}:
        pair = [item for item in artifact.results if item.scenario_id == scenario_id]
        assert pair[0].facts == pair[1].facts


def test_api_stage_and_model_call_count_fail_closed_before_overspend_or_proxying(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="no persisted execution"):
        _multi_model_calls([])
    with pytest.raises(RuntimeError, match="malformed"):
        _multi_model_calls(["execution-row"])
    with pytest.raises(RuntimeError, match="modelCallCount"):
        _multi_model_calls([{"modelId": _WRITER, "toolCalls": ["not-a-generation"]}])
    assert _multi_model_calls([{"modelCallCount": 0}, {"modelCallCount": 2}]) == 2
    with pytest.raises(RuntimeError, match="no successful model generations"):
        _multi_model_calls([{"modelCallCount": 0}])

    api = _Api()
    with (
        httpx.Client(
            base_url="https://fraudlens.invalid",
            headers={"Authorization": "Bearer synthetic-test-token"},
            transport=httpx.MockTransport(api),
        ) as client,
        pytest.raises(RuntimeError, match="hard USD cap"),
    ):
        run_api_stage(
            _scenarios(),
            load_sar_eval_config(),
            client=client,
            max_usd=Decimal("0.05"),
            clock=_Clock(),
            sleep=lambda _seconds: None,
            checkpoint_path=tmp_path / "runs.checkpoint.json",
        )
    assert not api.workflows


def test_api_checkpoint_requires_controlled_retry_and_preserves_completed_latency(
    tmp_path: Path,
) -> None:
    scenarios = _scenarios()
    config = load_sar_eval_config()
    checkpoint_path = tmp_path / "runs.checkpoint.json"
    api = _Api(fail_snapshot_for_run=2)
    with httpx.Client(
        base_url="https://fraudlens.invalid",
        headers={"Authorization": "Bearer synthetic-test-token"},
        transport=httpx.MockTransport(api),
    ) as client:
        with pytest.raises(RuntimeError, match="checkpoint retained"):
            run_api_stage(
                scenarios,
                config,
                client=client,
                max_usd=Decimal("10"),
                clock=_Clock(),
                sleep=lambda _seconds: None,
                checkpoint_path=checkpoint_path,
            )
        checkpoint = ApiRunCheckpoint.model_validate_json(checkpoint_path.read_text())
        assert len(checkpoint.results) == 1
        assert checkpoint.results[0].latency_ms == 1250
        assert checkpoint.failures[0].attempt_count == 1
        calls_before_rejected_resume = len(api.workflows)
        requests_before_rejected_resume = len(api.auth_headers)
        with pytest.raises(RuntimeError, match="explicit retry"):
            run_api_stage(
                scenarios,
                config,
                client=client,
                max_usd=Decimal("10"),
                clock=_Clock(),
                sleep=lambda _seconds: None,
                checkpoint_path=checkpoint_path,
            )
        assert len(api.workflows) == calls_before_rejected_resume
        assert len(api.auth_headers) == requests_before_rejected_resume
        resumed = run_api_stage(
            scenarios,
            config,
            client=client,
            max_usd=Decimal("10"),
            clock=_Clock(),
            sleep=lambda _seconds: None,
            checkpoint_path=checkpoint_path,
            retry_failed=True,
        )

    assert resumed.results[0] == checkpoint.results[0]
    assert resumed.results[0].latency_ms == 1250
    assert resumed.reserved_usd == Decimal("6.5")
    assert api.idempotency_keys[1] != api.idempotency_keys[2]
    first_failure = checkpoint.failures[0]
    assert api.idempotency_keys[1] == _idempotency_key(
        scenarios.run_id, first_failure.scenario_id, first_failure.arm, 1
    )
    assert api.idempotency_keys[2] == _idempotency_key(
        scenarios.run_id, first_failure.scenario_id, first_failure.arm, 2
    )
    assert _idempotency_key(
        scenarios.run_id, first_failure.scenario_id, first_failure.arm, 1
    ) == _idempotency_key(scenarios.run_id, first_failure.scenario_id, first_failure.arm, 1)


def test_api_checkpoint_recovers_inflight_crash_without_double_reserving(
    tmp_path: Path,
) -> None:
    scenarios = _scenarios()
    config = load_sar_eval_config()
    checkpoint_path = tmp_path / "runs.checkpoint.json"
    api = _Api(crash_after_post_for_run=1)
    with httpx.Client(
        base_url="https://fraudlens.invalid",
        headers={"Authorization": "Bearer synthetic-test-token"},
        transport=httpx.MockTransport(api),
    ) as client:
        with pytest.raises(SystemExit, match="simulated process crash"):
            run_api_stage(
                scenarios,
                config,
                client=client,
                max_usd=Decimal("10"),
                clock=_Clock(),
                sleep=lambda _seconds: None,
                checkpoint_path=checkpoint_path,
            )
        checkpoint = ApiRunCheckpoint.model_validate_json(checkpoint_path.read_text())
        assert not checkpoint.results
        assert not checkpoint.failures
        assert len(checkpoint.reservations) == 1
        resumed = run_api_stage(
            scenarios,
            config,
            client=client,
            max_usd=Decimal("10"),
            clock=_Clock(),
            sleep=lambda _seconds: None,
            checkpoint_path=checkpoint_path,
        )

    assert api.idempotency_keys[0] == api.idempotency_keys[1]
    assert resumed.reserved_usd == Decimal("6.4")
    final_checkpoint = ApiRunCheckpoint.model_validate_json(checkpoint_path.read_text())
    assert len(final_checkpoint.reservations) == 64


def test_api_checkpoint_allows_only_a_higher_resume_cap(tmp_path: Path) -> None:
    scenarios = _scenarios()
    config = load_sar_eval_config()
    checkpoint_path = tmp_path / "runs.checkpoint.json"
    api = _Api()
    with httpx.Client(
        base_url="https://fraudlens.invalid",
        headers={"Authorization": "Bearer synthetic-test-token"},
        transport=httpx.MockTransport(api),
    ) as client:
        with pytest.raises(RuntimeError, match="hard USD cap"):
            run_api_stage(
                scenarios,
                config,
                client=client,
                max_usd=Decimal("0.1005"),
                clock=_Clock(),
                sleep=lambda _seconds: None,
                checkpoint_path=checkpoint_path,
            )
        checkpoint = ApiRunCheckpoint.model_validate_json(checkpoint_path.read_text())
        assert len(checkpoint.results) == 1
        with pytest.raises(ValueError, match="cannot lower"):
            run_api_stage(
                scenarios,
                config,
                client=client,
                max_usd=Decimal("0.10"),
                clock=_Clock(),
                sleep=lambda _seconds: None,
                checkpoint_path=checkpoint_path,
            )
        resumed = run_api_stage(
            scenarios,
            config,
            client=client,
            max_usd=Decimal("10"),
            clock=_Clock(),
            sleep=lambda _seconds: None,
            checkpoint_path=checkpoint_path,
        )

    assert resumed.authorized_max_usd == Decimal("10")
    assert resumed.results[0] == checkpoint.results[0]


def test_api_artifact_round_trips_and_rejects_incomplete_or_overspent(tmp_path: Path) -> None:
    artifact = _api_runs(_scenarios())
    target = tmp_path / artifact.run_id / "runs.json"
    write_api_runs(target, artifact)
    assert load_api_runs(target) == artifact

    raw = artifact.model_dump(mode="json", by_alias=True)
    raw["results"][-1] = raw["results"][0]
    with pytest.raises(ValueError, match="exactly two arms"):
        ApiRunArtifact.model_validate(raw)
    raw = artifact.model_dump(mode="json", by_alias=True)
    raw["spentUsd"] = "11"
    with pytest.raises(ValueError, match="exceeds"):
        ApiRunArtifact.model_validate(raw)

    invalid = artifact.results[0].model_dump(mode="json", by_alias=True)
    invalid["writerModelId"] = "openrouter/openai/not-observed"
    with pytest.raises(ValueError, match="present in modelIds"):
        ApiArmResult.model_validate(invalid)
    invalid = artifact.results[0].model_dump(mode="json", by_alias=True)
    invalid["modelCalls"] = 2
    with pytest.raises(ValueError, match="must equal one"):
        ApiArmResult.model_validate(invalid)


def test_api_response_ingest_poll_and_draft_errors_fail_closed() -> None:
    request = httpx.Request("GET", "https://fraudlens.invalid/test")
    with pytest.raises(RuntimeError, match="status 500"):
        _body(httpx.Response(500, request=request), 200)
    with pytest.raises(RuntimeError, match="must be an object"):
        _body(httpx.Response(200, json=[], request=request), 200)

    failed_transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"status": "failed"})
    )
    with httpx.Client(base_url="https://fraudlens.invalid", transport=failed_transport) as client:
        with pytest.raises(RuntimeError, match="investigation failed"):
            _poll(
                client,
                "run-failed",
                timeout_s=1,
                poll_interval_s=0.01,
                clock=_Clock(),
                sleep=lambda _seconds: None,
            )
        with pytest.raises(RuntimeError, match="configured timeout"):
            _poll(
                client,
                "run-timeout",
                timeout_s=0.001,
                poll_interval_s=0.01,
                clock=_Clock(),
                sleep=lambda _seconds: None,
            )

    scenario = _scenarios().scenarios[0]
    with pytest.raises(RuntimeError, match="no persisted draft"):
        _arm_result(
            scenario,
            "single_writer",
            _snapshot("single_writer"),
            {"workflowMode": "single_writer"},
            1,
        )
    with pytest.raises(RuntimeError, match="no writer model"):
        detail = _detail("single_writer")
        detail["sarDraft"].pop("modelId")
        _arm_result(
            scenario,
            "single_writer",
            _snapshot("single_writer"),
            detail,
            1,
        )


def test_arm_result_rejects_workflow_fallback_cost_and_paired_fact_drift() -> None:
    scenario = _scenarios().scenarios[0]
    snapshot = _snapshot("single_writer")
    snapshot["workflowMode"] = "multi_agent"
    with pytest.raises(RuntimeError, match="snapshot workflowMode"):
        _arm_result(scenario, "single_writer", snapshot, _detail("single_writer"), 1)

    detail = _detail("single_writer")
    detail["sarDraft"]["workflow"] = "multi_agent"
    with pytest.raises(RuntimeError, match="sarDraft workflow"):
        _arm_result(scenario, "single_writer", _snapshot("single_writer"), detail, 1)

    detail = _detail("multi_agent")
    detail["sarDraft"]["costUsd"] = "0.0009"
    with pytest.raises(RuntimeError, match="equal persisted execution costs"):
        _arm_result(scenario, "multi_agent", _snapshot("multi_agent"), detail, 1)

    artifact = _api_runs(_scenarios())
    raw = artifact.model_dump(mode="json", by_alias=True)
    raw["results"][1]["facts"]["fraudProbability"] = 0.1
    with pytest.raises(ValueError, match="identical durable evaluation facts"):
        ApiRunArtifact.model_validate(raw)

    snapshot = _snapshot("single_writer")
    snapshot.pop("retrievedRegulations")
    with pytest.raises(RuntimeError, match="lacks persisted retrievedRegulations"):
        _arm_result(scenario, "single_writer", snapshot, _detail("single_writer"), 1)

    snapshot = _snapshot("single_writer")
    snapshot["retrievedRegulations"][0].pop("text")
    with pytest.raises(RuntimeError, match="retrievedRegulations is malformed"):
        _arm_result(scenario, "single_writer", snapshot, _detail("single_writer"), 1)


def test_persisted_latency_requires_ordered_aware_snapshot_timestamps() -> None:
    snapshot = _snapshot("single_writer")
    snapshot["createdAt"] = "2026-01-15T12:00:02+00:00"
    with pytest.raises(RuntimeError, match="invalid persisted latency"):
        _persisted_latency_ms(snapshot)

    snapshot = _snapshot("single_writer")
    snapshot["updatedAt"] = "not-a-time"
    with pytest.raises(RuntimeError, match="valid persisted latency"):
        _persisted_latency_ms(snapshot)


def test_duplicate_ingest_is_resolved_exactly_and_other_statuses_fail() -> None:
    scenario = _scenarios().scenarios[0]

    def duplicate(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(409)
        external_id = request.url.params["search"]
        transaction = next(
            item for item in scenario.transactions if item.external_id == external_id
        )
        return httpx.Response(
            200,
            json={
                "transactions": [
                    {
                        **transaction.model_dump(mode="json", by_alias=True),
                        "transactionId": f"id-{external_id}",
                    }
                ]
            },
        )

    with httpx.Client(
        base_url="https://fraudlens.invalid", transport=httpx.MockTransport(duplicate)
    ) as client:
        assert _ingest(client, scenario) == f"id-{scenario.subject_external_id}"

    def stale_collision(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(409)
        external_id = request.url.params["search"]
        transaction = next(
            item for item in scenario.transactions if item.external_id == external_id
        )
        row = transaction.model_dump(mode="json", by_alias=True)
        row.update({"transactionId": f"id-{external_id}", "amount": "1.00"})
        return httpx.Response(200, json={"transactions": [row]})

    with (
        httpx.Client(
            base_url="https://fraudlens.invalid",
            transport=httpx.MockTransport(stale_collision),
        ) as client,
        pytest.raises(RuntimeError, match="differs from the current scenario"),
    ):
        _ingest(client, scenario)

    with (
        httpx.Client(
            base_url="https://fraudlens.invalid",
            transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
        ) as client,
        pytest.raises(RuntimeError, match="ingest failed"),
    ):
        _ingest(client, scenario)


async def test_judge_stage_is_blind_deterministic_structured_three_sample_and_bounded(
    tmp_path: Path,
) -> None:
    scenarios = _scenarios()
    runs = _api_runs(scenarios)
    config = load_sar_eval_config()
    prompt = JudgePromptTemplate.load(config.judge.prompt_id)
    catalog = load_catalog(find_config_dir() / "llm" / "catalog.yml")
    first_client = _Judge()
    second_client = _Judge()

    first = await run_judge_stage(
        scenarios,
        runs,
        config,
        client=cast(JudgeClient, first_client),
        catalog=catalog,
        prompt=prompt,
        max_usd=Decimal("10"),
    )
    second = await run_judge_stage(
        scenarios,
        runs,
        config,
        client=cast(JudgeClient, second_client),
        catalog=catalog,
        prompt=prompt,
        max_usd=Decimal("10"),
    )

    assert len(first.samples) == 96
    assert [item.presented_order for item in first.samples] == [
        item.presented_order for item in second.samples
    ]
    assert len({item.presented_order for item in first.samples}) == 2
    assert all("response_schema" in kwargs for _messages, kwargs in first_client.calls)
    assert all(
        "single_writer" not in (message.content or "")
        and "multi_agent" not in (message.content or "")
        for messages, _kwargs in first_client.calls
        for message in cast(Sequence[LlmMessage], messages)
    )
    assert first.prompt_hash == prompt.prompt_hash
    assert first.model_family == "anthropic"
    assert first.spent_usd < first.authorized_max_usd
    evidence_message = cast(Sequence[LlmMessage], first_client.calls[0][0])[1].content or ""
    subject = scenarios.scenarios[0].transactions[-1]
    assert str(subject.amount) in evidence_message
    assert subject.channel in evidence_message
    assert subject.country in evidence_message
    assert scenarios.scenarios[0].expected_citation_ids[0] in evidence_message
    assert '"fraudProbability": 0.91' in evidence_message
    assert '"code": "structuring"' in evidence_message
    assert '"feature": "amount_log"' in evidence_message
    assert '"shapValue": 0.72' in evidence_message
    assert '"modelVersion": "v-test"' in evidence_message
    assert '"retrievedRegulations"' in evidence_message
    assert "No person shall structure a transaction." in evidence_message
    assert '"historicalSyntheticCount": 3' in evidence_message
    assert scenarios.scenarios[0].scenario_id in evidence_message
    assert scenarios.scenarios[1].scenario_id not in evidence_message
    assert "agentGeneratedConclusion" not in evidence_message
    assert "mustNotAppear" not in evidence_message
    target = tmp_path / first.run_id / "judgments.json"
    write_judgments(target, first)
    assert load_judgments(target) == first
    raw = first.model_dump(mode="json", by_alias=True)
    raw["spentUsd"] = "11"
    with pytest.raises(ValueError, match="spend exceeds"):
        JudgmentArtifact.model_validate(raw)

    raw = first.model_dump(mode="json", by_alias=True)
    raw["modelFamily"] = "openai"
    with pytest.raises(ValueError, match="modelFamily must match"):
        JudgmentArtifact.model_validate(raw)


def test_judge_structured_boundaries_and_provenance_reject_drift() -> None:
    with pytest.raises(ValueError, match="require a span"):
        ElementScore(element="who", present=True, quoted_span=None)
    names: tuple[ElementName, ...] = ("why", "where", "when", "what", "who")
    elements = tuple(
        ElementScore(element=element, present=True, quoted_span=element) for element in names
    )
    with pytest.raises(ValueError, match="canonical order"):
        CandidateScore(candidate="A", elements=elements)

    canonical = tuple(reversed(elements))
    candidate = CandidateScore(candidate="A", elements=canonical)
    with pytest.raises(ValueError, match="A and B"):
        JudgeResponse(candidates=(candidate, candidate))
    with pytest.raises(ValueError, match="router, family, and model"):
        model_family("missing-family")


async def test_judge_rejects_writer_family_match_and_reserves_before_call() -> None:
    scenarios = _scenarios()
    config = load_sar_eval_config()
    prompt = JudgePromptTemplate.load(config.judge.prompt_id)
    catalog = load_catalog(find_config_dir() / "llm" / "catalog.yml")
    client = _Judge()

    matched_results = tuple(
        item.model_copy(
            update={
                "writer_model_id": "openrouter/anthropic/claude-sonnet-4.6",
                "model_ids": ("openrouter/anthropic/claude-sonnet-4.6",),
            }
        )
        for item in _api_runs(scenarios).results
    )
    matched = _api_runs(scenarios).model_copy(update={"results": matched_results})
    with pytest.raises(ValueError, match="judge model family"):
        await run_judge_stage(
            scenarios,
            matched,
            config,
            client=cast(JudgeClient, client),
            catalog=catalog,
            prompt=prompt,
            max_usd=Decimal("10"),
        )
    assert not client.calls
    with pytest.raises(ValueError, match="share run and config identity"):
        await run_judge_stage(
            scenarios,
            _api_runs(scenarios).model_copy(update={"config_sha256": "0" * 64}),
            config,
            client=cast(JudgeClient, client),
            catalog=catalog,
            prompt=prompt,
            max_usd=Decimal("10"),
        )
    with pytest.raises(ValueError, match="current versioned prompt bytes"):
        await run_judge_stage(
            scenarios,
            _api_runs(scenarios),
            config,
            client=cast(JudgeClient, client),
            catalog=catalog,
            prompt=prompt.model_copy(update={"prompt_hash": "0" * 64}),
            max_usd=Decimal("10"),
        )
    runs = _api_runs(scenarios)
    drifted_facts = runs.results[1].facts.model_copy(update={"fraud_probability": 0.1})
    drifted_pair = runs.results[1].model_copy(update={"facts": drifted_facts})
    with pytest.raises(ValueError, match="identical durable evaluation facts"):
        await run_judge_stage(
            scenarios,
            runs.model_copy(update={"results": (runs.results[0], drifted_pair, *runs.results[2:])}),
            config,
            client=cast(JudgeClient, client),
            catalog=catalog,
            prompt=prompt,
            max_usd=Decimal("10"),
        )
    assert not client.calls
    with pytest.raises(RuntimeError, match="hard USD cap"):
        await run_judge_stage(
            scenarios,
            _api_runs(scenarios),
            config,
            client=cast(JudgeClient, client),
            catalog=catalog,
            prompt=prompt,
            max_usd=Decimal("0.01"),
        )
    assert not client.calls

    runs = _api_runs(scenarios)
    oversized = runs.results[0].model_copy(update={"narrative": "x" * 40_000})
    oversized_runs = runs.model_copy(update={"results": (oversized, *runs.results[1:])})
    with pytest.raises(RuntimeError, match="UTF-8 byte limit"):
        await run_judge_stage(
            scenarios,
            oversized_runs,
            config,
            client=cast(JudgeClient, client),
            catalog=catalog,
            prompt=prompt,
            max_usd=Decimal("10"),
        )
    assert not client.calls


async def test_judge_rejects_hallucinated_quote_spans() -> None:
    scenarios = _scenarios()
    config = load_sar_eval_config()
    prompt = JudgePromptTemplate.load(config.judge.prompt_id)
    catalog = load_catalog(find_config_dir() / "llm" / "catalog.yml")
    client = _Judge(hallucinated_span=True)

    with pytest.raises(RuntimeError, match="quoted span absent"):
        await run_judge_stage(
            scenarios,
            _api_runs(scenarios),
            config,
            client=cast(JudgeClient, client),
            catalog=catalog,
            prompt=prompt,
            max_usd=Decimal("10"),
        )
    assert len(client.calls) == 1

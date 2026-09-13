"""SAR evaluation transport, artifact, durable-fact, and pairing tests."""

from __future__ import annotations

import httpx
import pytest
from sar_eval_fakes import (
    _api_runs,
    _Clock,
    _detail,
    _facts,
    _scenarios,
    _snapshot,
)

from lib.sar_eval.runner import (
    ApiRunArtifact,
    _arm_result,
    _body,
    _ingest,
    _paired_facts_equal,
    _persisted_latency_ms,
    _poll,
)


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

    attempts = 0

    def timeout_once(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("transient synthetic timeout", request=request)
        return httpx.Response(200, json=_snapshot("single_writer", run_id="run-recovered"))

    with httpx.Client(
        base_url="https://fraudlens.invalid", transport=httpx.MockTransport(timeout_once)
    ) as client:
        recovered = _poll(
            client,
            "run-recovered",
            timeout_s=1,
            poll_interval_s=0.01,
            clock=_Clock(),
            sleep=lambda _seconds: None,
        )
    assert recovered["status"] == "completed"
    assert attempts == 2

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

    baseline = _facts()
    score_jitter = baseline.model_copy(
        update={
            "retrieved_regulations": (
                baseline.retrieved_regulations[0].model_copy(update={"score": 0.984}),
            )
        }
    )
    assert _paired_facts_equal(baseline, score_jitter)
    material_score_drift = score_jitter.model_copy(
        update={
            "retrieved_regulations": (
                score_jitter.retrieved_regulations[0].model_copy(update={"score": 0.95}),
            )
        }
    )
    assert not _paired_facts_equal(baseline, material_score_drift)
    changed_chunk = score_jitter.model_copy(
        update={
            "retrieved_regulations": (
                score_jitter.retrieved_regulations[0].model_copy(update={"text": "Changed text."}),
            )
        }
    )
    assert not _paired_facts_equal(baseline, changed_chunk)

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

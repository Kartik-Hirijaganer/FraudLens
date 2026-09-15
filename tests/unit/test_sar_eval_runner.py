"""SAR evaluation API runner spend, checkpoint, and resume tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sar_eval_fakes import (
    _WRITER,
    _Api,
    _api_runs,
    _Clock,
    _scenarios,
)

from lib.sar_eval.config import load_sar_eval_config
from lib.sar_eval.runner import (
    ApiArmResult,
    ApiRunArtifact,
    ApiRunCheckpoint,
    _idempotency_key,
    _multi_model_calls,
    load_api_runs,
    run_api_stage,
    write_api_runs,
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

"""Summary: Stable orchestration facade for the real-API paired SAR evaluation stage.

Key classes:
- (none)

Key functions:
- run_api_stage: execute randomized paired arms with bounded resumable spend.
- write_api_runs: atomically serialize a completed run.
- load_api_runs: strictly parse a completed run.

Notes:
- Existing private test seams remain available through explicit aliases.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import httpx

from lib.sar_eval.config import SarEvalConfig
from lib.sar_eval.runner_checkpoint import (
    checkpoint_path as default_checkpoint_path,
)
from lib.sar_eval.runner_checkpoint import (
    load_checkpoint,
    write_checkpoint,
)
from lib.sar_eval.runner_contracts import (
    ApiArmFailure,
    ApiArmReservation,
    ApiArmResult,
    ApiRunArtifact,
    ApiRunCheckpoint,
    Arm,
    DurableEvaluationFacts,
    RetrievedRegulationFact,
    ScenarioToolEvidence,
    ToolEvidenceFact,
    _TerminalInvestigationError,
    paired_facts_equal,
)
from lib.sar_eval.runner_facts import (
    arm_order,
    arm_result,
    idempotency_key,
    multi_model_calls,
    persisted_latency_ms,
    scenario_tool_evidence,
)
from lib.sar_eval.runner_transport import atomic_write, ingest, poll, response_body
from lib.sar_eval.scenarios import ScenarioArtifact, validate_scenario_binding

_body = response_body
_ingest = ingest
_poll = poll
_multi_model_calls = multi_model_calls
_arm_result = arm_result
_idempotency_key = idempotency_key
_persisted_latency_ms = persisted_latency_ms
_paired_facts_equal = paired_facts_equal

__all__ = [
    "ApiArmFailure",
    "ApiArmReservation",
    "ApiArmResult",
    "ApiRunArtifact",
    "ApiRunCheckpoint",
    "Arm",
    "DurableEvaluationFacts",
    "RetrievedRegulationFact",
    "ScenarioToolEvidence",
    "ToolEvidenceFact",
    "_arm_result",
    "_body",
    "_idempotency_key",
    "_ingest",
    "_multi_model_calls",
    "_paired_facts_equal",
    "_persisted_latency_ms",
    "_poll",
    "load_api_runs",
    "run_api_stage",
    "write_api_runs",
]


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
    target = checkpoint_path or default_checkpoint_path(config, scenarios.run_id)
    checkpoint = load_checkpoint(target, scenarios, config, max_usd)
    results = list(checkpoint.results)
    failure_map = {(item.scenario_id, item.arm): item for item in checkpoint.failures}
    reservations = list(checkpoint.reservations)
    reservation_keys = {(item.scenario_id, item.arm, item.attempt) for item in reservations}
    result_keys = {(item.scenario_id, item.arm) for item in results}
    spent = sum((item.cost_usd for item in results), Decimal("0"))
    reserved = sum((item.amount_usd for item in reservations), Decimal("0"))
    for scenario in scenarios.scenarios:
        order = arm_order(config.seed, scenario.scenario_id)
        if all((scenario.scenario_id, arm) in result_keys for arm in order):
            continue
        if not retry_failed and any(
            (scenario.scenario_id, arm) in failure_map
            for arm in order
            if (scenario.scenario_id, arm) not in result_keys
        ):
            raise RuntimeError("checkpoint contains a failed arm; explicit retry is required")
        transaction_id = ingest(client, scenario)
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
                write_checkpoint(
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
                        "modelOverride": config.calibration.model_version,
                        "workflowMode": arm,
                    },
                    headers={
                        "Idempotency-Key": idempotency_key(
                            scenarios.run_id, scenario.scenario_id, arm, attempt
                        )
                    },
                )
                run_id = str(response_body(response, 202)["runId"])
                snapshot = poll(
                    client,
                    run_id,
                    timeout_s=config.api.run_timeout_s,
                    poll_interval_s=config.api.poll_interval_s,
                    clock=clock,
                    sleep=sleep,
                )
                terminal_observed = True
                latency_ms = persisted_latency_ms(snapshot)
                alert_id = snapshot.get("alertId")
                if not isinstance(alert_id, str) or not alert_id:
                    raise RuntimeError("evaluation scenario did not raise an alert")
                detail = response_body(client.get(f"/api/v1/alerts/{alert_id}"), 200)
                observed = arm_result(scenario, arm, snapshot, detail, latency_ms)
                if observed.facts.model_version != config.calibration.model_version:
                    raise RuntimeError("API arm did not use the protocol-pinned scoring model")
                if observed.cost_usd > reserve:
                    raise RuntimeError("one API arm exceeded the configured per-run cost cap")
                paired = [item for item in results if item.scenario_id == scenario.scenario_id]
                if paired and not paired_facts_equal(paired[0].facts, observed.facts):
                    raise RuntimeError("paired snapshots expose different durable evaluation facts")
            except Exception as exc:
                if terminal_observed or isinstance(exc, _TerminalInvestigationError):
                    failure_map[key] = ApiArmFailure(
                        scenario_id=scenario.scenario_id,
                        arm=arm,
                        attempt_count=attempt,
                        error_code="arm_failed",
                    )
                write_checkpoint(
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
            write_checkpoint(
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
        scenario_tool_evidence=scenario_tool_evidence(tuple(results)),
    )


def write_api_runs(path: Path, artifact: ApiRunArtifact) -> None:
    """Atomically serialize completed API observations."""
    if path.parent.name != artifact.run_id:
        raise ValueError("API artifact path must be nested under its exact run id")
    atomic_write(path, artifact)


def load_api_runs(path: Path) -> ApiRunArtifact:
    """Strictly parse completed API observations."""
    artifact = ApiRunArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    if artifact.run_id != path.parent.name:
        raise ValueError("API artifact run id does not match the requested CLI run id")
    return artifact

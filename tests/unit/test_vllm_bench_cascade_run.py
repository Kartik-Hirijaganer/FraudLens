"""Summary: Scenario execution and replay-pilot projection tests for the gated benchmark.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Scenarios drive a scripted `SarDrafter` that returns the same attempt rows the production
  cascade records, so a level's evidence is shaped exactly as a live run's would be.
- Nothing here contacts a provider, a GPU, or a socket, and no test asserts a number it did not
  recompute from the fixture run.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from cascade_fakes import (
    BrokenSampler,
    ScriptedCascadeDrafter,
    cascade_artifact,
    cascade_config,
    ledger_row,
    replay_manifest,
)
from vllm_bench_fakes import HASH, NOW, FakeSampler, replay_gate, server, small_config

from fraudlens_backend.sar.factory import load_sar_llm_config
from fraudlens_ml.sar import SarDraftResult, SarDraftStatus, SarGateReason
from lib.experiments.budget import load_budget_config
from lib.vllm_bench.cascade import UNSERVED_STAGE
from lib.vllm_bench.cascade_load import (
    _measurements,
    prepared_inputs,
    role_command_prefix,
    role_telemetry,
    run_scenario,
    run_scenario_level,
)
from lib.vllm_bench.cascade_pilot import project_replay_pilot
from lib.vllm_bench.config import VllmBenchConfig, load_config
from lib.vllm_bench.scenarios import ScenarioConfig
from lib.vllm_bench.state import CaseArtifact, LevelCheckpoint, case_artifact_sha256, load_run

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _sar_config(config: VllmBenchConfig):
    """Load the production SAR routing the scenarios measure."""
    return load_sar_llm_config(_REPO_ROOT / "config" / config.cascade.sar_config_file)


def _pilot(config: VllmBenchConfig, artifact: CaseArtifact, manifest, **overrides):
    """Project the free replay pilot from one persisted run with committed governance inputs."""
    arguments = {
        "manifest": manifest,
        "artifact": artifact,
        "config": config,
        "sar_config": _sar_config(config),
        "budget": load_budget_config(_REPO_ROOT),
        "ledger": ledger_row(manifest.run_id),
        "gate": replay_gate(),
        "profile": "awq-bf16",
    }
    arguments.update(overrides)
    return project_replay_pilot(**arguments)


async def _level(
    config: VllmBenchConfig,
    artifact: CaseArtifact,
    drafter: ScriptedCascadeDrafter,
    *,
    scenario_name: str = "awq-bf16",
) -> LevelCheckpoint:
    """Run one cascade scenario level against the scripted drafter."""
    scenario = config.cascade.scenario(scenario_name)
    return await run_scenario_level(
        scenario=scenario,
        concurrency=2,
        artifact=artifact,
        cases_sha256=case_artifact_sha256(artifact),
        config=config,
        profile="full",
        drafter=drafter,
        samplers={role: FakeSampler() for role in scenario.endpoints},
    )


async def test_scenario_level_records_every_attempt_and_samples_both_endpoints() -> None:
    """A two-endpoint scenario measures both GPUs and keeps the rejected tier in evidence."""
    config = small_config(load_config())
    artifact = cascade_artifact(config)
    drafter = ScriptedCascadeDrafter(escalate={"case-1"})

    checkpoint = await _level(config, artifact, drafter)

    assert checkpoint.scenario == "awq-bf16"
    assert len(checkpoint.measurements) == 3
    assert [item.stage for item in checkpoint.measurements if item.case_id == "case-1"] == [
        "awq",
        "bf16",
    ]
    assert {item.gate_passed for item in checkpoint.measurements} == {True, False}
    assert len(checkpoint.telemetry) == len(config.cascade.scenario("awq-bf16").endpoints)
    assert drafter.warmups == 1
    assert drafter.calls == 2


async def test_only_the_served_attempt_carries_text_so_rejected_output_is_never_retained() -> None:
    """A rejected tier's draft must not reach the evidence any more than it reaches a client."""
    config = small_config(load_config())
    drafter = ScriptedCascadeDrafter(escalate={"case-1"})

    checkpoint = await _level(config, cascade_artifact(config), drafter)

    escalated = [item for item in checkpoint.measurements if item.case_id == "case-1"]
    assert escalated[0].content == ""
    assert escalated[0].error_code is None
    assert escalated[0].usage is not None
    assert escalated[0].gate_reasons == ("citation_fabricated",)
    assert escalated[1].content != ""


def test_scenario_level_refuses_a_corpus_without_production_sar_inputs() -> None:
    """A cascade cannot be driven by rendered messages alone; the corpus must carry SAR inputs."""
    config = small_config(load_config())
    artifact = cascade_artifact(config)
    stripped = artifact.model_copy(
        update={
            "cases": tuple(case.model_copy(update={"sar_input": None}) for case in artifact.cases)
        }
    )

    with pytest.raises(ValueError, match="no SAR input"):
        prepared_inputs(stripped, config, profile="full", concurrency=2)


async def test_a_case_whose_drafter_never_terminates_is_recorded_not_dropped() -> None:
    """Silently shortening a paid level is the exact evidence failure this harness prevents."""
    config = small_config(load_config())
    drafter = ScriptedCascadeDrafter(no_terminal={"case-1"})

    checkpoint = await _level(config, cascade_artifact(config), drafter)

    failed = [item for item in checkpoint.measurements if item.case_id == "case-1"]
    assert [item.error_code for item in failed] == ["cascade_case_error"]
    assert {item.case_id for item in checkpoint.measurements} == {"case-0", "case-1"}


def test_preflight_failure_retains_its_gate_reason_without_a_model_attempt() -> None:
    """A zero-spend policy rejection must remain explainable in the final cascade report."""
    verdict = replay_gate().rejected(SarGateReason.NO_CITATIONS)
    result = SarDraftResult(
        status=SarDraftStatus.FAILED,
        model_id="awq",
        prompt_version="v5",
        prompt_hash=HASH,
        error_code="sar_quality_gate_failed",
        quality=verdict,
    )

    (measurement,) = _measurements("case-0", 0, 1700, NOW, result)

    assert measurement.stage == UNSERVED_STAGE
    assert measurement.gate_reasons == ("no_citations",)
    assert measurement.usage is None


async def test_a_failed_warm_up_stops_the_level_before_it_is_measured() -> None:
    """Warm-up exclusion only means anything if a broken warm-up refuses to start the level."""
    config = small_config(load_config())
    drafter = ScriptedCascadeDrafter(no_terminal={"warmup-0"})

    with pytest.raises(RuntimeError, match="warm-up request failed"):
        await _level(config, cascade_artifact(config), drafter)


async def test_a_gate_rejected_warm_up_still_proves_the_request_path() -> None:
    """A deterministic rejection is a measured gate verdict, not a serving failure."""
    config = small_config(load_config())
    drafter = ScriptedCascadeDrafter(escalate={"warmup-0"})

    checkpoint = await _level(config, cascade_artifact(config), drafter)

    assert checkpoint.warmup_completed == 1
    assert drafter.calls == 2


async def test_scenario_resume_keeps_completed_levels_and_remeasures_nothing(
    tmp_path: Path,
) -> None:
    """Resuming after an interruption must never re-measure a level already paid for."""
    config = small_config(load_config())
    artifact = cascade_artifact(config)
    scenario = ScenarioConfig(
        name="awq-bf16", profile="awq-bf16", endpoints=("awq", "bf16"), concurrency_levels=(1, 2)
    )
    run_path = tmp_path / "run.json"
    samplers = {role: FakeSampler() for role in scenario.endpoints}
    arguments = {
        "run_path": run_path,
        "run_id": "vllm-bench-0123456789abcdef",
        "scenario": scenario,
        "artifact": artifact,
        "cases_sha256": case_artifact_sha256(artifact),
        "config": config,
        "profile": "full",
        "samplers": samplers,
    }

    created: list[ScriptedCascadeDrafter] = []

    def fresh_drafter() -> ScriptedCascadeDrafter:
        drafter = ScriptedCascadeDrafter()
        created.append(drafter)
        return drafter

    first = await run_scenario(**arguments, drafter_factory=fresh_drafter)
    first_factory_calls = len(created)
    second = await run_scenario(**arguments, drafter_factory=fresh_drafter)

    assert set(first.levels) == {"awq-bf16:1", "awq-bf16:2"}
    assert first.completed_at is not None
    assert load_run(run_path).levels.keys() == first.levels.keys()
    assert second.levels["awq-bf16:1"] == first.levels["awq-bf16:1"]
    assert first_factory_calls == 2
    assert all(drafter.calls == 2 for drafter in created)
    assert len(created) == first_factory_calls


async def test_a_resumed_run_bound_to_another_corpus_fails_closed(tmp_path: Path) -> None:
    """Resuming across a corpus change would silently compare two different populations."""
    config = small_config(load_config())
    artifact = cascade_artifact(config)
    scenario = config.cascade.scenario("awq-bf16")
    run_path = tmp_path / "run.json"
    arguments = {
        "run_path": run_path,
        "run_id": "vllm-bench-0123456789abcdef",
        "scenario": scenario,
        "artifact": artifact,
        "config": config,
        "profile": "full",
        "samplers": {role: FakeSampler() for role in scenario.endpoints},
        "drafter_factory": ScriptedCascadeDrafter,
    }
    await run_scenario(**arguments, cases_sha256=case_artifact_sha256(artifact))

    with pytest.raises(ValueError, match="manifest identity drifted"):
        await run_scenario(**arguments, cases_sha256=HASH)


def test_replay_pilot_reproduces_its_rates_and_costs_the_declared_matrix() -> None:
    """The pilot is an admission projection: every rate and hour recomputes from the run."""
    config = cascade_config(small_config(load_config()))
    artifact, manifest = replay_manifest(config)

    pilot = _pilot(config, artifact, manifest)

    level = pilot.levels[-1]
    assert pilot.run_id == manifest.run_id
    assert pilot.replayed_stages == ("awq", "bf16")
    assert pilot.policy_version == replay_gate().policy.policy_version
    # One of two cases fabricates a citation on the AWQ arm and is clean on BF16.
    assert level.cascade.escalation_rate == pytest.approx(0.5)
    assert level.cascade.final_pass_rate == pytest.approx(1.0)
    assert level.arm_pass_rate["awq"] == pytest.approx(0.5)
    assert level.arm_pass_rate["bf16"] == pytest.approx(1.0)
    assert {item.scenario for item in pilot.matrix} == {
        scenario.name for scenario in config.cascade.scenarios
    }
    assert pilot.admitted is (pilot.cost_with_margin_usd <= pilot.allocation_usd)


def test_the_projection_includes_the_overhead_the_prior_session_actually_billed() -> None:
    """Projecting measurement time alone would understate every paid hour outside a level."""
    config = cascade_config(small_config(load_config()))
    artifact, manifest = replay_manifest(config)

    lean = _pilot(config, artifact, manifest, ledger=ledger_row(manifest.run_id, Decimal("0.001")))
    heavy = _pilot(config, artifact, manifest, ledger=ledger_row(manifest.run_id, Decimal("50")))

    assert lean.session_overhead_ratio == 1
    assert heavy.session_overhead_ratio > 1
    assert heavy.projected_cost_usd > lean.projected_cost_usd
    assert heavy.billed_hours == Decimal("50")


def test_a_pilot_without_exactly_one_ledger_session_fails_closed() -> None:
    """An unledgered or duplicated session makes the overhead unattributable."""
    config = cascade_config(small_config(load_config()))
    artifact, manifest = replay_manifest(config)

    with pytest.raises(ValueError, match="exactly one ledger resource session"):
        _pilot(config, artifact, manifest, ledger=ledger_row("vllm-bench-ffffffffffffffff"))


def test_replay_pilot_refuses_a_run_that_never_measured_every_stage() -> None:
    """A projection built from a partial matrix would not be the cascade it claims to project."""
    config = cascade_config(small_config(load_config()))
    artifact, manifest = replay_manifest(config)
    partial = manifest.model_copy(
        update={"levels": {key: value for key, value in manifest.levels.items() if "awq" in key}}
    )

    with pytest.raises(ValueError, match="no level covering every replayable stage"):
        _pilot(config, artifact, partial)


def test_a_single_stage_profile_has_nothing_to_replay() -> None:
    """The pilot projects a cascade; a one-stage profile must say so rather than project itself."""
    config = cascade_config(small_config(load_config()))
    artifact, manifest = replay_manifest(config)

    pilot = _pilot(config, artifact, manifest, profile="awq-raw")

    assert pilot.replayed_stages == ("awq",)
    assert pilot.levels[0].cascade.escalation_rate == 0.0


def test_the_pilot_refuses_to_cost_a_scenario_level_the_run_never_measured() -> None:
    """Costing an unmeasured level would admit a matrix on evidence that does not exist."""
    config = small_config(load_config())
    artifact, manifest = replay_manifest(config)

    with pytest.raises(ValueError, match="unmeasured concurrency levels"):
        _pilot(config, artifact, manifest)


def test_a_role_samples_its_own_endpoint_through_env_indirection(monkeypatch) -> None:
    """Two provisioned endpoints must be sampled independently, with no address in config."""
    config = load_config()
    assert config.cascade.endpoints["awq"].telemetry_prefix_env == ("VLLM_AWQ_TELEMETRY_PREFIX")
    assert config.cascade.endpoints["bf16"].telemetry_prefix_env == ("VLLM_BF16_TELEMETRY_PREFIX")

    monkeypatch.delenv("VLLM_AWQ_TELEMETRY_PREFIX", raising=False)
    monkeypatch.delenv("VLLM_BF16_TELEMETRY_PREFIX", raising=False)
    assert role_telemetry(config, "awq").command_prefix == config.telemetry.command_prefix
    assert role_telemetry(config, "bf16").command_prefix == config.telemetry.command_prefix

    monkeypatch.setenv("VLLM_AWQ_TELEMETRY_PREFIX", "ssh awq-host --")
    monkeypatch.setenv("VLLM_BF16_TELEMETRY_PREFIX", "ssh bf16-host --")
    assert role_command_prefix(config, "awq") == ("ssh", "awq-host", "--")
    assert role_command_prefix(config, "bf16") == ("ssh", "bf16-host", "--")
    assert role_telemetry(config, "awq").command_prefix == ("ssh", "awq-host", "--")
    assert role_telemetry(config, "bf16").command_prefix == ("ssh", "bf16-host", "--")


async def test_scenario_provenance_is_bound_per_role_and_fails_closed_on_drift(
    tmp_path: Path,
) -> None:
    """Each endpoint's pins are recorded, and a restart that changed them cannot be resumed."""
    config = cascade_config(small_config(load_config()))
    artifact = cascade_artifact(config)
    scenario = config.cascade.scenario("awq-bf16")
    provenance = {
        role: server(config, config.cascade.endpoints[role].arm) for role in scenario.endpoints
    }
    arguments = {
        "run_path": tmp_path / "run.json",
        "run_id": "vllm-bench-0123456789abcdef",
        "scenario": scenario,
        "artifact": artifact,
        "cases_sha256": case_artifact_sha256(artifact),
        "config": config,
        "profile": "full",
        "samplers": {role: FakeSampler() for role in scenario.endpoints},
    }

    manifest = await run_scenario(
        **arguments, drafter_factory=ScriptedCascadeDrafter, provenance=provenance
    )

    assert set(manifest.servers) == {"awq", "bf16"}
    drifted = {
        role: value.model_copy(update={"weight_memory_gib": value.weight_memory_gib * 2})
        for role, value in provenance.items()
    }
    with pytest.raises(ValueError, match="weight memory drift"):
        await run_scenario(**arguments, drafter_factory=ScriptedCascadeDrafter, provenance=drifted)


async def test_one_endpoints_sampler_failing_degrades_telemetry_without_losing_the_level() -> None:
    """A paid level must not be thrown away because one endpoint's sampler stopped answering."""
    config = cascade_config(small_config(load_config()))
    artifact = cascade_artifact(config)
    scenario = config.cascade.scenario("awq-bf16")
    samplers = {"awq": FakeSampler(), "bf16": BrokenSampler()}

    checkpoint = await run_scenario_level(
        scenario=scenario,
        concurrency=2,
        artifact=artifact,
        cases_sha256=case_artifact_sha256(artifact),
        config=config,
        profile="full",
        drafter=ScriptedCascadeDrafter(),
        samplers=samplers,
    )

    assert {sample.role for sample in checkpoint.telemetry} == {"awq"}
    assert len(checkpoint.measurements) == 2


async def test_a_tier_that_fails_transport_is_recorded_with_its_code_and_no_usage() -> None:
    """An escalation caused by a provider error must stay distinguishable from a gate rejection."""
    config = cascade_config(small_config(load_config()))
    drafter = ScriptedCascadeDrafter(escalate={"case-1"}, transport_error={"case-1"})

    checkpoint = await _level(config, cascade_artifact(config), drafter)

    rejected = next(
        item
        for item in checkpoint.measurements
        if item.case_id == "case-1" and item.attempt_ordinal == 0
    )
    assert rejected.error_code == "provider_unavailable"
    assert rejected.usage is None
    assert rejected.gate_reasons == ()


async def test_each_attempt_records_the_route_model_and_policy_that_produced_it() -> None:
    """A rate is only auditable if the exact connection, served model, and rules are recorded."""
    config = cascade_config(small_config(load_config()))

    checkpoint = await _level(
        config, cascade_artifact(config), ScriptedCascadeDrafter(escalate={"case-1"})
    )

    escalated = [item for item in checkpoint.measurements if item.case_id == "case-1"]
    assert [item.connection for item in escalated] == ["runpod-awq", "runpod-bf16"]
    assert all(item.policy_hash for item in escalated)
    assert len({item.policy_hash for item in escalated}) == 1

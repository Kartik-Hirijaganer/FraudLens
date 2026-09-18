"""Summary: Cascade composition, level-metric derivation, and scenario-contract tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Every assertion recomputes its expected value from the fixture data, so no metric can be green
  against a number typed into the test.
- Scenario execution and the replay pilot are covered by `test_vllm_bench_cascade_run.py`.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from cascade_fakes import attempt
from pydantic import ValidationError
from vllm_bench_fakes import HASH, NOW, benchmark_case, replay_gate, telemetry

from lib.vllm_bench.cascade import (
    UNSERVED_STAGE,
    CascadeCase,
    CascadeMetrics,
    CascadeStageTotals,
    cascade_metrics,
    compose_cases,
)
from lib.vllm_bench.config import load_config
from lib.vllm_bench.metrics import build_level_metrics
from lib.vllm_bench.scenarios import CascadeConfig
from lib.vllm_bench.state import LevelCheckpoint

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _checkpoint(measurements: tuple) -> LevelCheckpoint:
    """Wrap per-attempt measurements in one complete cascade level checkpoint."""
    return LevelCheckpoint(
        arm="awq",
        scenario="awq-bf16",
        concurrency=2,
        case_order_sha256=HASH,
        cases_sha256=HASH,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=4),
        warmup_completed=1,
        measurements=measurements,
    )


def test_composition_pairs_every_attempt_with_its_case_and_sums_case_latency() -> None:
    """A case that escalated pays both tiers, and its latency is the sum it actually paid."""
    served = attempt("case-0", 0, "awq", passed=True, latency_s=2.0)
    rejected = attempt("case-1", 0, "awq", passed=False, latency_s=3.0)
    escalated = attempt("case-1", 1, "bf16", passed=True, latency_s=4.0)

    composed = {case.case_id: case for case in compose_cases((served, rejected, escalated))}

    assert composed["case-0"].stages == ("awq",)
    assert composed["case-0"].escalation_tier == 0
    assert composed["case-0"].latency_ms == pytest.approx(2000)
    assert composed["case-1"].stages == ("awq", "bf16")
    assert composed["case-1"].served_stage == "bf16"
    assert composed["case-1"].escalation_tier == 1
    assert composed["case-1"].latency_ms == pytest.approx(7000)


def test_exhausted_cascade_keeps_its_reason_codes_and_names_no_serving_stage() -> None:
    """Every tier rejecting is a terminal failure that still carries why it failed."""
    first = attempt("case-0", 0, "awq", passed=False, reasons=("citation_fabricated",))
    second = attempt("case-0", 1, "bf16", passed=False, reasons=("no_citations",))

    (case,) = compose_cases((first, second))

    assert not case.passed
    assert case.served_stage is None
    assert case.reasons == ("no_citations",)
    assert case.escalation_tier == 1


def test_non_contiguous_attempts_are_rejected_rather_than_silently_composed() -> None:
    """A missing attempt means a dropped measurement; composing it would hide evidence loss."""
    with pytest.raises(ValueError, match="non-contiguous"):
        compose_cases((attempt("case-0", 1, "bf16", passed=True),))


def test_preflight_rejection_is_a_zero_attempt_case_not_a_model_stage() -> None:
    """A policy rejection before generation is a terminal case without model accounting."""
    preflight = attempt(
        "case-0", 0, UNSERVED_STAGE, passed=False, reasons=("no_citations",), latency_s=0
    ).model_copy(update={"error_code": "sar_quality_gate_failed", "usage": None})

    (case,) = compose_cases((preflight,))
    metrics = cascade_metrics((case,), stages=("awq", "bf16"), measurements=(preflight,))

    assert case.stages == ()
    assert case.escalation_tier is None
    assert metrics.final_pass_rate == 0
    assert metrics.terminal_failure_rate == 1
    assert metrics.escalation_rate == 0
    assert metrics.reason_counts == {"no_citations": 1}
    assert metrics.stage_totals.latency_ms == {"awq": 0.0, "bf16": 0.0}
    assert metrics.stage_totals.errors == {"awq": {}, "bf16": {}}


def test_preflight_rejection_counts_as_a_case_but_not_as_a_model_request() -> None:
    """Request/error throughput must not fabricate a provider call for a preflight stop."""
    case_a = benchmark_case("case-0")
    case_b = benchmark_case("case-1")
    served = attempt("case-0", 0, "awq", passed=True, sequence=0)
    preflight = attempt(
        "case-1", 0, UNSERVED_STAGE, passed=False, reasons=("no_citations",), sequence=1
    ).model_copy(update={"error_code": "sar_quality_gate_failed", "usage": None})

    metrics = build_level_metrics(
        _checkpoint((served, preflight)),
        cases={case_a.case_id: case_a, case_b.case_id: case_b},
        hourly_rates_usd={"pay_as_you_go": 3.6},
        purchase_option="pay_as_you_go",
        drafts_per_unit=1000,
        quality_policy=load_config().quality,
        gate=replay_gate(),
        stages=("awq", "bf16"),
        endpoints=2,
    )

    assert metrics.requests == 1
    assert metrics.successful == 1
    assert metrics.error_rate == 0
    assert metrics.quality.evaluated == 1
    assert metrics.cascade is not None
    assert metrics.cascade.cases == 2
    assert metrics.cascade.terminal_failure_rate == 0.5


def test_all_preflight_rejections_report_zero_model_requests_without_division() -> None:
    """A population stopped before generation has zero calls and therefore zero call errors."""
    case = benchmark_case("case-0")
    preflight = attempt("case-0", 0, UNSERVED_STAGE, passed=False).model_copy(
        update={"error_code": "sar_quality_gate_failed", "usage": None}
    )

    metrics = build_level_metrics(
        _checkpoint((preflight,)),
        cases={case.case_id: case},
        hourly_rates_usd={"pay_as_you_go": 3.6},
        purchase_option="pay_as_you_go",
        drafts_per_unit=1000,
        quality_policy=load_config().quality,
        gate=replay_gate(),
        stages=("awq", "bf16"),
        endpoints=2,
    )

    assert metrics.requests == 0
    assert metrics.successful == 0
    assert metrics.error_rate == 0
    assert metrics.quality.evaluated == 0


def test_preflight_marker_cannot_be_mixed_with_model_attempts() -> None:
    """A synthetic zero-attempt marker plus a model call would double-count one case."""
    preflight = attempt("case-0", 0, UNSERVED_STAGE, passed=False)
    generated = attempt("case-0", 1, "awq", passed=True)

    with pytest.raises(ValueError, match="mixes a preflight rejection"):
        compose_cases((preflight, generated))


def test_rates_are_measured_over_reaching_cases_against_the_configured_stages() -> None:
    """Stage pass rates divide by the cases that reached a stage, not by the population."""
    attempts = (
        attempt("case-0", 0, "awq", passed=True),
        attempt("case-1", 0, "awq", passed=False),
        attempt("case-1", 1, "bf16", passed=True),
        attempt("case-2", 0, "awq", passed=False),
        attempt("case-2", 1, "bf16", passed=False),
        attempt("case-2", 2, "external", passed=False, reasons=("citation_fabricated",)),
    )

    metrics = cascade_metrics(compose_cases(attempts), stages=("awq", "bf16", "external"))

    assert metrics.stage_pass_rate["awq"] == pytest.approx(1 / 3)
    assert metrics.stage_pass_rate["bf16"] == pytest.approx(1 / 2)
    assert metrics.stage_pass_rate["external"] == 0.0
    assert metrics.stage_mix == pytest.approx({"awq": 1 / 3, "bf16": 1 / 3, "external": 0.0})
    assert metrics.escalation_rate == pytest.approx(2 / 3)
    assert metrics.external_rate == pytest.approx(1 / 3)
    assert metrics.final_pass_rate == pytest.approx(2 / 3)
    assert metrics.terminal_failure_rate == pytest.approx(1 / 3)
    assert metrics.reason_counts == {"citation_fabricated": 1}


def test_a_stage_nothing_reached_reports_zero_instead_of_vanishing() -> None:
    """An unreached third tier must stay visible in the table as a real zero."""
    metrics = cascade_metrics(
        compose_cases((attempt("case-0", 0, "awq", passed=True),)),
        stages=("awq", "bf16", "external"),
    )

    assert metrics.stage_pass_rate["external"] == 0.0
    assert metrics.external_rate == 0.0
    assert metrics.stage_mix["bf16"] == 0.0


def test_unconfigured_stage_or_empty_population_fails_closed() -> None:
    """Rates against stages the protocol never declared would be unattributable evidence."""
    with pytest.raises(ValueError, match="unconfigured stages"):
        cascade_metrics(
            compose_cases((attempt("case-0", 0, "mystery", passed=True),)), stages=("awq",)
        )
    with pytest.raises(ValueError, match="at least one composed case"):
        cascade_metrics((), stages=("awq",))


def test_level_metrics_count_model_calls_but_normalize_cost_and_gpu_time_per_case() -> None:
    """Raw throughput counts calls; cost and GPU hours belong to the case a draft serves."""
    case_a = benchmark_case("case-0")
    case_b = benchmark_case("case-1")
    checkpoint = _checkpoint(
        (
            attempt("case-0", 0, "awq", passed=True, sequence=0),
            attempt("case-1", 0, "awq", passed=False, sequence=1),
            attempt("case-1", 1, "bf16", passed=True, sequence=1),
        )
    )

    metrics = build_level_metrics(
        checkpoint,
        cases={case_a.case_id: case_a, case_b.case_id: case_b},
        hourly_rates_usd={"pay_as_you_go": 3.6},
        purchase_option="pay_as_you_go",
        drafts_per_unit=1000,
        quality_policy=load_config().quality,
        gate=replay_gate(),
        stages=("awq", "bf16"),
        endpoints=2,
    )

    assert metrics.requests == 3
    assert metrics.cascade is not None
    assert metrics.cascade.cases == 2
    assert metrics.cascade.escalation_rate == pytest.approx(0.5)
    # Two endpoints across a four-second window is eight endpoint-seconds over two served cases.
    assert metrics.gpu_hours_per_case == pytest.approx(4 * 2 / 3600 / 2)
    assert metrics.cost_usd == pytest.approx(3.6 * 2 * 4 / 3600)
    assert metrics.cost_per_1000_drafts_usd == pytest.approx(metrics.cost_usd / 2 * 1000)
    assert metrics.quality.evaluated == 2


def test_a_raw_level_has_no_cascade_block_and_prices_one_endpoint() -> None:
    """A single-model scenario must not acquire cascade rates it never measured."""
    case = benchmark_case("case-0")
    checkpoint = _checkpoint((attempt("case-0", 0, "awq", passed=True, sequence=0),))

    metrics = build_level_metrics(
        checkpoint,
        cases={case.case_id: case},
        hourly_rates_usd={"pay_as_you_go": 3.6},
        purchase_option="pay_as_you_go",
        drafts_per_unit=1000,
        quality_policy=load_config().quality,
        gate=replay_gate(),
    )

    assert metrics.cascade is None
    assert metrics.gpu_hours_per_case == pytest.approx(4 / 3600)
    assert metrics.cost_usd == pytest.approx(3.6 * 4 / 3600)


def test_scenario_matrix_rejects_duplicate_names_profiles_and_unknown_roles() -> None:
    """A scenario matrix that cannot be resolved would measure something unattributable."""
    base = {
        "sar_config_file": "llm/sar-vllm.yml",
        "replay_run_id": "vllm-bench-0123456789abcdef",
        "replay_policy": "replay_gate",
        "allocation": "gpu_benchmark",
        "rate_key": "runpod_rtx4090_secure_payg",
        "endpoints": {"awq": {"arm": "awq", "connection": "runpod-awq"}},
    }
    one = {"name": "a", "profile": "awq-raw", "endpoints": ["awq"], "concurrency_levels": [1]}

    with pytest.raises(ValidationError, match="scenario names must be unique"):
        CascadeConfig.model_validate(
            {**base, "scenarios": [one, {**one, "profile": "bf16-baseline"}]}
        )
    with pytest.raises(ValidationError, match="distinct SAR profile"):
        CascadeConfig.model_validate({**base, "scenarios": [one, {**one, "name": "b"}]})
    with pytest.raises(ValidationError, match="unknown endpoint roles"):
        CascadeConfig.model_validate({**base, "scenarios": [{**one, "endpoints": ["bf16"]}]})
    with pytest.raises(ValidationError, match="must be unique"):
        CascadeConfig.model_validate({**base, "scenarios": [{**one, "concurrency_levels": [1, 1]}]})


def test_scenario_lookup_and_endpoint_count_fail_closed_on_an_unknown_name() -> None:
    """Operating an undeclared scenario must be impossible, not silently defaulted."""
    cascade = load_config().cascade

    assert cascade.endpoint_count("awq-bf16") == 2
    assert cascade.endpoint_count("awq-constrained") == 1
    assert cascade.endpoint_count("bf16-constrained") == 1
    assert cascade.scenario("awq-raw").profile == "awq-raw"
    with pytest.raises(ValueError, match="unknown benchmark scenario"):
        cascade.scenario("not-declared")


def test_composed_case_invariants_reject_impossible_outcomes() -> None:
    """A composed case that both failed and named a serving stage would misreport the cascade."""
    fields = {
        "case_id": "case-0",
        "stages": ("awq",),
        "escalation_tier": 0,
        "latency_ms": 1.0,
        "prompt_tokens": 1,
        "completion_tokens": 1,
    }

    with pytest.raises(ValidationError, match="must name the stage"):
        CascadeCase(**fields, passed=True, served_stage=None)
    with pytest.raises(ValidationError, match="cannot name a serving stage"):
        CascadeCase(**fields, passed=False, served_stage="awq")
    with pytest.raises(ValidationError, match="must index an attempted stage"):
        CascadeCase(**{**fields, "escalation_tier": 3}, passed=False)
    with pytest.raises(ValidationError, match="preflight failure"):
        CascadeCase(**{**fields, "stages": (), "escalation_tier": 0}, passed=False)


def test_cascade_rate_totals_must_account_for_every_case() -> None:
    """Pass and failure rates that do not sum to one would leave cases unaccounted for."""
    with pytest.raises(ValidationError, match="cover every case"):
        CascadeMetrics(
            cases=2,
            stage_pass_rate={"awq": 1.0},
            stage_mix={"awq": 1.0},
            escalation_rate=0.0,
            external_rate=0.0,
            final_pass_rate=1.0,
            terminal_failure_rate=0.5,
            latency_p50_ms=1.0,
            latency_p95_ms=1.0,
            latency_p99_ms=1.0,
            gpu_seconds_per_case=1.0,
            stage_totals=CascadeStageTotals(
                latency_ms={"awq": 1.0},
                prompt_tokens={"awq": 1},
                completion_tokens={"awq": 1},
                retries={"awq": 0},
                errors={"awq": {}},
                cost_usd={"awq": Decimal("0")},
            ),
        )


def test_per_stage_accounting_attributes_latency_tokens_retries_and_errors() -> None:
    """What escalation costs is only visible if a rejected tier's own spend is kept per stage."""
    attempts = (
        attempt("case-0", 0, "awq", passed=True, latency_s=2.0, sequence=0),
        attempt("case-1", 0, "awq", passed=False, latency_s=3.0, sequence=1),
        attempt(
            "case-1", 1, "bf16", passed=True, latency_s=4.0, sequence=1, cost_usd=Decimal("0.004")
        ),
    )
    failed = attempts[1].model_copy(update={"usage": None, "error_code": "rate_limited"})
    measurements = (attempts[0], failed, attempts[2])

    metrics = cascade_metrics(
        compose_cases(measurements), stages=("awq", "bf16"), measurements=measurements
    )

    totals = metrics.stage_totals
    assert totals.latency_ms == pytest.approx({"awq": 5000.0, "bf16": 4000.0})
    assert totals.prompt_tokens == {"awq": 10, "bf16": 10}
    assert totals.completion_tokens == {"awq": 10, "bf16": 10}
    assert totals.retries == {"awq": 0, "bf16": 0}
    assert totals.errors == {"awq": {"rate_limited": 1}, "bf16": {}}
    assert totals.cost_usd == {"awq": Decimal("0"), "bf16": Decimal("0.004")}


def test_a_two_endpoint_level_reports_each_role_and_their_summed_footprint() -> None:
    """A cascade occupies both GPUs at once, so its device footprint is both peaks together."""
    case_a = benchmark_case("case-0")
    case_b = benchmark_case("case-1")
    checkpoint = _checkpoint(
        (
            attempt("case-0", 0, "awq", passed=True, sequence=0),
            attempt("case-1", 0, "awq", passed=False, sequence=1),
            attempt("case-1", 1, "bf16", passed=True, sequence=1),
        )
    ).model_copy(
        update={
            "telemetry": (
                telemetry().model_copy(update={"role": "awq", "memory_used_mib": 6_000}),
                telemetry().model_copy(update={"role": "bf16", "memory_used_mib": 15_000}),
            )
        }
    )

    metrics = build_level_metrics(
        checkpoint,
        cases={case_a.case_id: case_a, case_b.case_id: case_b},
        hourly_rates_usd={"pay_as_you_go": 3.6},
        purchase_option="pay_as_you_go",
        drafts_per_unit=1000,
        quality_policy=load_config().quality,
        gate=replay_gate(),
        stages=("awq", "bf16"),
        endpoints=2,
    )

    assert set(metrics.telemetry_by_role) == {"awq", "bf16"}
    assert metrics.telemetry_by_role["awq"].memory_peak_mib == 6_000
    assert metrics.aggregate_memory_peak_mib == 21_000
    assert metrics.telemetry.samples == 2

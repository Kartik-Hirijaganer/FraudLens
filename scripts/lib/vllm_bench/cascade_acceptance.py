"""Summary: The gated-cascade publication contract — every criterion a scenario-shaped report
must satisfy before it may be published (release 0.5.0 Phase 5, carried risk 11). Kept separate
from the builder so what the project is willing to publish can be read, reviewed, and changed
without reading how the numbers are derived.

`gate_verdict_parity` is the criterion that retires the v1 cross-check: a live scenario records the
production gate's verdict on every attempt, the report re-derives that verdict from the persisted
output, and publication requires exact agreement. A quality figure that survives this is evidence
about the shipped path, not a second opinion about it.

Key classes:
- (none)

Key functions:
- policy_hash: the single quality-policy hash every judged attempt was recorded under.
- parity_failures: attempts whose re-derived verdict disagrees with the recorded live verdict.
- serving_errors: transport and serving failures, which are never the gate rejecting a draft.
- cascade_acceptance: derive every publication criterion from measured data.

Notes:
- Serving errors and gate rejections are counted separately. A case no stage would serve is a
  quality outcome the cascade is DESIGNED to produce; a transport failure is not, and collapsing
  them would let a broken endpoint read as a strict model.
- Reference validity is measured over ACCEPTED drafts. Averaging it across rejected attempts too
  would report the quality of output the product never served.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fraudlens_backend.sar.quality_gate import SarQualityGate
from lib.vllm_bench.cascade import UNSERVED_STAGE
from lib.vllm_bench.cascade_report_models import CascadeProvenance, ScenarioReport
from lib.vllm_bench.config import VllmBenchConfig
from lib.vllm_bench.quality import case_verdict
from lib.vllm_bench.report_models import AcceptanceCheck
from lib.vllm_bench.scenarios import ScenarioConfig
from lib.vllm_bench.state import BenchmarkCase, RunManifest


def parity_failures(
    manifest: RunManifest,
    cases: Mapping[str, BenchmarkCase],
    gate: SarQualityGate,
    executed: Sequence[tuple[ScenarioConfig, tuple[int, ...]]],
) -> int:
    """Count attempts whose re-derived gate verdict disagrees with what the live run recorded."""
    mismatches = 0
    for scenario, measured in executed:
        for concurrency in measured:
            for item in manifest.levels[f"{scenario.name}:{concurrency}"].measurements:
                case = cases.get(item.case_id)
                if item.gate_passed is None or case is None or item.stage == UNSERVED_STAGE:
                    continue
                if case_verdict(case, item, gate).passed != item.gate_passed:
                    mismatches += 1
    return mismatches


def policy_hash(manifest: RunManifest) -> str:
    """Return the single quality-policy hash every judged attempt was recorded under."""
    hashes = {
        item.policy_hash
        for checkpoint in manifest.levels.values()
        for item in checkpoint.measurements
        if item.policy_hash is not None
    }
    if len(hashes) != 1:
        raise ValueError("a cascade report requires exactly one recorded quality-policy hash")
    return hashes.pop()


def serving_errors(reports: Sequence[ScenarioReport]) -> int:
    """Count transport and serving failures, which are never the gate rejecting a draft."""
    return sum(
        count
        for scenario in reports
        for level in scenario.levels
        if level.cascade is not None
        for codes in level.cascade.stage_totals.errors.values()
        for count in codes.values()
    )


def cascade_acceptance(  # noqa: PLR0913 - the publication contract stays explicit and independently read.
    config: VllmBenchConfig,
    *,
    reports: Sequence[ScenarioReport],
    executed: Sequence[tuple[ScenarioConfig, tuple[int, ...]]],
    weight_reduction: float,
    parity_mismatches: int,
    provenance: CascadeProvenance,
) -> tuple[AcceptanceCheck, ...]:
    """Derive every publication criterion from measured data, never from an authored claim."""
    cascades = [item for item in reports if len(item.stages) > 1]
    levels = [level for item in cascades for level in item.levels if level.cascade is not None]
    final_pass = min(
        (level.cascade.final_pass_rate for level in levels if level.cascade is not None),
        default=0.0,
    )
    validity = min((item.quality.reference_validity for item in cascades), default=0.0)
    drift = max(
        (level.token_accounting_drift for item in reports for level in item.levels),
        default=0.0,
    )
    roles_with_telemetry = {
        role
        for item in reports
        for level in item.levels
        for role, summary in level.telemetry_by_role.items()
        if summary.samples > 0
    }
    required_roles = {role for item in reports for role in item.endpoint_roles}
    measured = ", ".join(
        f"{scenario.name}@{'/'.join(str(value) for value in levels_)}"
        for scenario, levels_ in executed
    )
    acceptance = config.acceptance
    return (
        AcceptanceCheck(
            name="scenario_matrix_measured",
            passed=bool(cascades),
            observed=measured,
            required="at least one multi-stage scenario measured at a declared level",
        ),
        AcceptanceCheck(
            name="gate_verdict_parity",
            passed=parity_mismatches == 0,
            observed=f"{parity_mismatches} attempts disagree",
            required="0 attempts disagree",
        ),
        AcceptanceCheck(
            name="zero_serving_errors",
            passed=serving_errors(reports) == 0,
            observed=f"{serving_errors(reports)} serving errors",
            required="0 serving errors",
        ),
        AcceptanceCheck(
            name="cascade_final_pass_rate",
            passed=final_pass >= acceptance.cascade_final_pass_rate_min,
            observed=f"{final_pass:.4f}",
            required=f">= {acceptance.cascade_final_pass_rate_min}",
        ),
        AcceptanceCheck(
            name="accepted_reference_validity",
            passed=validity >= config.quality.reference_validity_min,
            observed=f"{validity:.4f}",
            required=f">= {config.quality.reference_validity_min}",
        ),
        AcceptanceCheck(
            name="token_accounting_drift",
            passed=drift <= acceptance.token_accounting_drift_max,
            observed=f"{drift:.4f}",
            required=f"<= {acceptance.token_accounting_drift_max}",
        ),
        AcceptanceCheck(
            name="gpu_telemetry_per_role",
            passed=(not acceptance.require_gpu_telemetry) or required_roles <= roles_with_telemetry,
            observed=f"sampled {sorted(roles_with_telemetry)}",
            required=f"sampled {sorted(required_roles)}",
        ),
        AcceptanceCheck(
            name="weight_memory_reduction",
            passed=weight_reduction >= acceptance.weight_memory_reduction_min,
            observed=f"{weight_reduction:.4f}",
            required=f">= {acceptance.weight_memory_reduction_min}",
        ),
        AcceptanceCheck(
            name="single_quality_policy",
            passed=True,
            observed=provenance.quality_policy_sha256,
            required="exactly one recorded policy hash",
        ),
    )

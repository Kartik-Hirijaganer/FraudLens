"""Summary: Deterministic SAR schema, citation, fact, abstention, and support tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Fabricated references are asserted before any grounding/filtering stage.
"""

from __future__ import annotations

from vllm_bench_fakes import benchmark_case, measurement, replay_gate, sar_response

from lib.vllm_bench.config import load_config
from lib.vllm_bench.quality import evaluate_case, summarize_quality


def test_grounded_output_passes_all_quality_dimensions() -> None:
    case = benchmark_case()
    result = evaluate_case(case, measurement(case, 0), load_config().quality, replay_gate())
    assert result.schema_valid
    assert result.reference_validity == 1
    assert result.citation_recall == 1
    assert result.required_fact_coverage == 1
    assert result.fabricated_reference_attempts == 0
    assert result.unsupported_claim_flags == 0
    assert result.useful


def test_error_invalid_schema_fabrication_and_truncation_fail_usefulness() -> None:
    case = benchmark_case()
    policy = load_config().quality
    gate = replay_gate()
    errored = evaluate_case(case, measurement(case, 0, error_code="http_error"), policy, gate)
    assert not errored.schema_valid
    assert not errored.useful

    invalid_measurement = measurement(case, 0).model_copy(
        update={"content": "not-json", "finish_reason": "length"}
    )
    invalid = evaluate_case(case, invalid_measurement, policy, gate)
    assert invalid.truncated
    assert not invalid.schema_valid

    fabricated_measurement = measurement(case, 0).model_copy(
        update={"content": sar_response(fabricated=True)}
    )
    fabricated = evaluate_case(case, fabricated_measurement, policy, gate)
    assert fabricated.fabricated_reference_attempts == 1
    assert fabricated.reference_validity == 0
    assert fabricated.unsupported_claim_flags == 0
    assert not fabricated.useful


def test_abstention_and_aggregate_quality() -> None:
    measured = benchmark_case()
    abstention = benchmark_case("abstention", "abstention")
    policy = load_config().quality
    gate = replay_gate()
    correct = evaluate_case(abstention, measurement(abstention, 1), policy, gate)
    assert correct.abstention_correct is True
    # Abstaining is the RIGHT behaviour on an evidence-free case and is scored as such, but it is
    # not a servable draft: the shipped gate rejects an uncited SAR, and the cascade refuses such a
    # case in preflight without spending. Useful throughput must not count what nothing serves.
    assert not correct.useful

    wrong = measurement(abstention, 1).model_copy(update={"content": sar_response()})
    wrong_result = evaluate_case(abstention, wrong, policy, gate)
    assert wrong_result.abstention_correct is False
    assert not wrong_result.useful

    cases = {measured.case_id: measured, abstention.case_id: abstention}
    summary, details = summarize_quality(
        cases, (measurement(measured, 0), measurement(abstention, 1)), policy, gate
    )
    assert summary.evaluated == 2
    assert summary.schema_valid_rate == 1
    assert summary.abstention_correctness == 1
    assert summary.useful_count == 1
    assert len(details) == 2

    empty, empty_details = summarize_quality({}, (), policy, gate)
    assert empty.evaluated == 0
    assert empty.abstention_correctness == 0
    assert empty_details == ()

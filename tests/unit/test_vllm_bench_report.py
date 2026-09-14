"""Summary: Complete-matrix validation, acceptance, quality delta, and headline tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Reports are derived exclusively from typed synthetic checkpoints.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError
from vllm_bench_fakes import complete_benchmark, measurement

from lib.vllm_bench.config import load_config
from lib.vllm_bench.report import build_report, load_report, write_report
from lib.vllm_bench.report_models import VllmBenchReport


def test_complete_matrix_produces_accepted_mechanical_report(sandbox) -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    report = build_report(manifest, artifact, config)
    assert report.acceptance_met
    assert all(check.passed for check in report.acceptance)
    assert report.weight_memory_reduction == pytest.approx(1 - 5 / 14)
    assert report.safetensors_reduction > 0.5
    assert report.measured_cases == 2
    assert report.abstention_cases == 1
    assert report.token_cost_comparison is not None
    assert set(report.arms[0].levels[0].cost_per_1000_drafts_usd_by_purchase_option) == {
        "spot",
        "pay_as_you_go",
    }
    assert "Acceptance NOT met" not in report.headline

    write_report(sandbox, report)
    assert load_report(sandbox / "report.json") == report
    assert (sandbox / "report.md").is_file()


def test_failures_and_slower_awq_are_disclosed_mechanically() -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    checkpoint = manifest.levels["awq:2"]
    cases = {case.case_id: case for case in artifact.cases}
    failed = measurement(cases[checkpoint.measurements[0].case_id], 0, error_code="http_error")
    slower = checkpoint.model_copy(
        update={
            "completed_at": checkpoint.completed_at + timedelta(seconds=2),
            "measurements": (failed, checkpoint.measurements[1]),
        }
    )
    primary_checkpoint = manifest.levels["awq:1"]
    primary_failed = measurement(
        cases[primary_checkpoint.measurements[0].case_id], 0, error_code="http_error"
    )
    primary = primary_checkpoint.model_copy(
        update={"measurements": (primary_failed, primary_checkpoint.measurements[1])}
    )
    levels = {**manifest.levels, "awq:1": primary, "awq:2": slower}
    report = build_report(manifest.model_copy(update={"levels": levels}), artifact, config)
    assert not report.acceptance_met
    assert "Acceptance NOT met" in report.headline
    assert "AWQ slower" in report.headline
    assert any(delta.warning for delta in report.quality_deltas)


def test_report_rejects_incomplete_identity_and_order_drift() -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    with pytest.raises(ValueError, match="both benchmark arms"):
        build_report(manifest.model_copy(update={"completed_at": None}), artifact, config)
    with pytest.raises(ValueError, match="configuration hash drifted"):
        build_report(
            manifest,
            artifact.model_copy(update={"config_sha256": "b" * 64}),
            config,
        )
    missing = {key: value for key, value in manifest.levels.items() if key != "awq:2"}
    with pytest.raises(ValueError, match="matrix is incomplete"):
        build_report(manifest.model_copy(update={"levels": missing}), artifact, config)
    right = manifest.levels["awq:1"].model_copy(update={"case_order_sha256": "b" * 64})
    with pytest.raises(ValueError, match="case order differs"):
        build_report(
            manifest.model_copy(update={"levels": {**manifest.levels, "awq:1": right}}),
            artifact,
            config,
        )
    shared_bad_hash = "b" * 64
    both = {
        **manifest.levels,
        "bf16:1": manifest.levels["bf16:1"].model_copy(
            update={"case_order_sha256": shared_bad_hash}
        ),
        "awq:1": manifest.levels["awq:1"].model_copy(update={"case_order_sha256": shared_bad_hash}),
    }
    with pytest.raises(ValueError, match="checkpoint identity"):
        build_report(manifest.model_copy(update={"levels": both}), artifact, config)


def test_report_rejects_cross_arm_provenance_drift_and_missing_gpu_evidence() -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    awq_server = manifest.servers["awq"].model_copy(update={"gpu_name": "Different GPU"})
    with pytest.raises(ValueError, match="not comparable"):
        build_report(
            manifest.model_copy(update={"servers": {**manifest.servers, "awq": awq_server}}),
            artifact,
            config,
        )

    levels = {
        key: checkpoint.model_copy(update={"telemetry": ()})
        for key, checkpoint in manifest.levels.items()
    }
    report = build_report(manifest.model_copy(update={"levels": levels}), artifact, config)
    telemetry_check = next(item for item in report.acceptance if item.name == "gpu_telemetry")
    assert not telemetry_check.passed


def test_report_model_rejects_authored_headline_or_acceptance_flag() -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    report = build_report(manifest, artifact, config)
    with pytest.raises(ValidationError, match="headline"):
        VllmBenchReport(**{**report.model_dump(), "headline": "authored claim"})
    with pytest.raises(ValidationError, match="acceptanceMet"):
        VllmBenchReport(**{**report.model_dump(), "acceptance_met": False})


def test_extra_checkpoint_fails_complete_matrix_acceptance() -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    levels = {**manifest.levels, "bf16:99": manifest.levels["bf16:2"]}
    report = build_report(manifest.model_copy(update={"levels": levels}), artifact, config)
    complete = next(check for check in report.acceptance if check.name == "complete_matrix")
    assert not complete.passed
    assert "complete_matrix" in report.headline

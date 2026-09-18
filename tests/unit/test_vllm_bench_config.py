"""Summary: vLLM benchmark protocol and typed-state validation tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Tests mutate only in-memory payloads and isolated artifact paths.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import yaml
from pydantic import ValidationError
from vllm_bench_fakes import HASH, NOW, RUN_ID, benchmark_case, complete_benchmark

from lib.quality.config import load_quality_config
from lib.study import canonical_json
from lib.vllm_bench.config import (
    DEFAULT_VLLM_BENCH_CONFIG,
    VllmBenchConfig,
    load_config,
    resolve_case_set,
    resolve_profile,
)
from lib.vllm_bench.state import (
    CaseArtifact,
    LevelCheckpoint,
    RequestMeasurement,
    TokenUsage,
    case_artifact_sha256,
    initialize_run,
    load_case_artifact,
    load_case_bundle,
    load_run,
    validate_restart_memory,
    write_case_artifact,
    write_case_bundle,
    write_run,
)


def _payload() -> dict[str, object]:
    return load_config().model_dump(mode="json", by_alias=True, exclude={"config_sha256"})


def test_config_pins_full_protocol_and_profiles() -> None:
    config = load_config()
    assert config.protocol_version == "vllm-sar-bench-v6"
    assert "vllm-sar-bench-v2" in config.protocol_lineage
    assert config.protocol_lineage["vllm-sar-bench-v3"] == (
        "4f9388d51ec47b0ef1f806e8784e56afcde3c67a66c42ba7d14bdcebdf4f5534"
    )
    assert config.protocol_lineage["vllm-sar-bench-v4"] == (
        "47349665261b5c0f893b156227003e7192d40928df78bb674fb1796186f6c8cf"
    )
    # The 1,000-case full run measured v5. Its exact config bytes stay recorded so the published
    # cascade evidence keeps reporting against what it actually executed under (AD-1.3).
    assert config.protocol_lineage["vllm-sar-bench-v5"] == (
        "57df143fde48dd7cfee6c14d366ea261d8bfa36c949e1be5ad5cbf0d7d70f169"
    )
    assert resolve_profile(config, "full") == (1000, (1, 8, 32), 10)
    assert resolve_profile(config, "smoke") == (8, (1, 2), 1)
    assert resolve_profile(config, "development") == (40, (32,), 1)
    assert resolve_case_set(config, "development") == "development"
    assert resolve_case_set(config, "full") == "measured"
    assert config.arms["bf16"].dtype == "bfloat16"
    assert config.arms["awq"].quantization == "awq_marlin"
    assert config.cascade.scenario("awq-constrained").endpoints == ("awq",)
    assert config.cascade.scenario("bf16-constrained").endpoints == ("bf16",)
    assert config.cascade.report.baseline == "bf16-baseline"
    assert config.acceptance.cascade_final_pass_rate_min == 0.99
    assert config.server.enable_prefix_caching is False
    assert config.application_pass.base_url_env == "FRAUDLENS_E2E_BASE_URL"
    assert config.application_pass.auth_token_env == "FRAUDLENS_E2E_AUTH_TOKEN"
    assert config.request.temperature == 0
    with pytest.raises(ValueError, match="unknown benchmark profile"):
        resolve_profile(config, "missing")
    with pytest.raises(ValueError, match="unknown benchmark profile"):
        resolve_case_set(config, "missing")


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda value: value["cases"].update({"count": 999}), "sum to cases.count"),
        (lambda value: value["cases"].update({"strata": ["amount_band"] * 2}), "duplicates"),
        (lambda value: value["arms"].pop("awq"), "exactly bf16 and awq"),
        (
            lambda value: value["arms"]["bf16"].update({"quantization": "awq_marlin"}),
            "bf16 arm",
        ),
        (
            lambda value: value["arms"]["awq"].update({"quantization": None}),
            "awq arm",
        ),
        (
            lambda value: value["arms"]["awq"].update({"tokenizer_revision": "b" * 40}),
            "identical pinned tokenizer",
        ),
        (lambda value: value["profiles"].pop("smoke"), "smoke and full"),
        (
            lambda value: value["profiles"].update({"full": {"cases_count": 2}}),
            "must not override",
        ),
        (
            lambda value: value["cost"].update({"default_host": "missing"}),
            "must exist",
        ),
        (
            lambda value: value["load"].update({"concurrency_levels": [2, 1]}),
            "positive, unique, and increasing",
        ),
        (
            lambda value: value["request"].update({"allowed_plain_http_hosts": ["8.8.8.8"]}),
            "loopback or private",
        ),
        (
            lambda value: value["server"].update({"process_bind_host": "0.0.0.0"}),
            "must be loopback",
        ),
        (
            lambda value: value["server"].update({"docker_bind_host": "127.0.0.1"}),
            "must accept mapped-port traffic",
        ),
        (
            lambda value: value["paths"].update({"output_dir": "../outside"}),
            "below .local",
        ),
    ),
)
def test_config_rejects_protocol_drift(mutation, message: str) -> None:
    payload = _payload()
    mutation(payload)
    with pytest.raises(ValidationError, match=message):
        VllmBenchConfig.model_validate(payload)


def test_case_and_checkpoint_invariants() -> None:
    case = benchmark_case()
    with pytest.raises(ValidationError, match="subset"):
        case.model_copy(update={"expected_citation_ids": ("missing",)}, deep=True).__class__(
            **{
                **case.model_dump(),
                "expected_citation_ids": ("missing",),
            }
        )
    with pytest.raises(ValidationError, match="remove citations"):
        type(case)(**{**case.model_dump(), "case_set": "abstention"})
    with pytest.raises(ValidationError, match="successful measurements"):
        RequestMeasurement(
            case_id="case", sequence=0, seed=1, attempts=1, started_at=NOW, latency_s=1
        )
    with pytest.raises(ValidationError, match="failed measurements"):
        RequestMeasurement(
            case_id="case",
            sequence=0,
            seed=1,
            attempts=1,
            started_at=NOW,
            latency_s=1,
            error_code="x",
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


def test_artifact_and_run_round_trip_and_resume(sandbox) -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    case_path = sandbox / "cases.json"
    file_hash = write_case_artifact(case_path, artifact)
    loaded, loaded_hash = load_case_artifact(case_path)
    assert loaded == artifact
    assert loaded_hash == file_hash
    assert case_artifact_sha256(artifact) == case_artifact_sha256(loaded)
    bundled_path = sandbox / "bundled.json"
    assert write_case_bundle(bundled_path, artifact) == load_case_bundle(bundled_path)[1]
    bundled_path.write_text(bundled_path.read_text().replace("ibm-final-test", "sar-eval"))
    with pytest.raises(ValueError, match="SHA or lineage drifted"):
        load_case_bundle(bundled_path)

    run_path = sandbox / "run.json"
    initialized = initialize_run(
        run_path,
        run_id=RUN_ID,
        config=config,
        profile="full",
        cases_sha256=HASH,
        started_at=NOW,
    )
    assert load_run(run_path) == initialized
    assert (
        initialize_run(
            run_path,
            run_id=RUN_ID,
            config=config,
            profile="full",
            cases_sha256=HASH,
            started_at=NOW + timedelta(days=1),
        )
        == initialized
    )
    with pytest.raises(ValueError, match="identity drifted"):
        initialize_run(
            run_path,
            run_id=RUN_ID,
            config=config,
            profile="full",
            cases_sha256="b" * 64,
            started_at=NOW,
        )
    write_run(run_path, manifest)
    assert canonical_json(load_run(run_path)) == canonical_json(manifest)


def test_restart_memory_and_level_validation() -> None:
    validate_restart_memory(10, 10.1)
    with pytest.raises(ValueError, match="positive"):
        validate_restart_memory(0, 1)
    with pytest.raises(ValueError, match="one percent"):
        validate_restart_memory(10, 10.2)
    case = benchmark_case()
    measurement = RequestMeasurement(
        case_id=case.case_id,
        sequence=1,
        seed=1,
        attempts=1,
        started_at=NOW,
        latency_s=1,
        ttft_s=0.1,
        usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )
    with pytest.raises(ValidationError, match="contiguous"):
        LevelCheckpoint(
            arm="bf16",
            concurrency=1,
            case_order_sha256=HASH,
            cases_sha256=HASH,
            started_at=NOW,
            completed_at=NOW,
            warmup_completed=0,
            measurements=(measurement,),
        )
    with pytest.raises(ValidationError, match="cannot precede"):
        LevelCheckpoint(
            arm="bf16",
            concurrency=1,
            case_order_sha256=HASH,
            cases_sha256=HASH,
            started_at=NOW,
            completed_at=NOW - timedelta(seconds=1),
            warmup_completed=0,
            measurements=(measurement.model_copy(update={"sequence": 0}),),
        )


def test_case_artifact_rejects_duplicate_ids_and_bad_subject_accounting() -> None:
    case = benchmark_case()
    base = {
        "protocol_version": "v1",
        "profile": "smoke",
        "case_source": "sar-eval",
        "config_sha256": HASH,
        "upstream_sha256": HASH,
        "prompt_version": "v1",
        "prompt_sha256": HASH,
        "distinct_subjects": 0,
        "subject_overlap": 0,
        "cases": (case, case),
    }
    with pytest.raises(ValidationError, match="unique"):
        CaseArtifact(**base)
    base["cases"] = (case,)
    with pytest.raises(ValidationError, match="subject accounting"):
        CaseArtifact(**base)


def test_shared_quality_thresholds_have_exactly_one_owner() -> None:
    """The benchmark and the CI gate suites must never judge by different floors.

    `config/vllm-bench.yaml` used to restate citation precision and required-fact coverage under
    its own names. Two copies of a threshold drift, and the benchmark would then call a draft
    useful that the shipped quality suite rejects.
    """
    shared = load_quality_config().sar_quality
    resolved = load_config().quality

    assert resolved.reference_validity_min == shared.citation_precision_min
    assert resolved.coverage_warn_min == shared.required_fact_coverage_min
    declared = yaml.safe_load(DEFAULT_VLLM_BENCH_CONFIG.read_text(encoding="utf-8"))["quality"]
    assert "reference_validity_min" not in declared
    assert "coverage_warn_min" not in declared

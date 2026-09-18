"""Summary: Deterministic request-order and per-arm server-binding tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Level EXECUTION is tested in `test_vllm_bench_cascade_run.py`, against the scenario runner
  that drives the production quality-gated drafter. Concurrency, warm-up exclusion, resume,
  corpus drift, and provenance drift all have their equivalents there; the model-only runner
  they used to be tested through was retired in release 0.5.0.
"""

from __future__ import annotations

import pytest
from vllm_bench_fakes import (
    HASH,
    benchmark_case,
    complete_benchmark,
    server,
    small_config,
)

from lib.vllm_bench.config import load_config
from lib.vllm_bench.load import bind_server, ordered_cases
from lib.vllm_bench.state import CaseArtifact


def _artifact(config) -> CaseArtifact:
    return CaseArtifact(
        protocol_version=config.protocol_version,
        profile="full",
        case_source="ibm-final-test",
        config_sha256=config.config_sha256,
        upstream_sha256=HASH,
        prompt_version="v1",
        prompt_sha256=HASH,
        distinct_subjects=2,
        subject_overlap=0,
        cases=(
            benchmark_case("case-0"),
            benchmark_case("case-1"),
            benchmark_case("dev-0").model_copy(update={"case_set": "development"}),
            benchmark_case("warmup-0", "warmup"),
            benchmark_case("abstention-0", "abstention"),
        ),
    )


def test_order_is_deterministic_and_fails_on_case_deficiency() -> None:
    config = small_config(load_config())
    artifact = _artifact(config)
    assert ordered_cases(artifact, config, profile="full", concurrency=2) == ordered_cases(
        artifact, config, profile="full", concurrency=2
    )
    deficient = artifact.model_copy(update={"cases": artifact.cases[1:]})
    with pytest.raises(ValueError, match="required"):
        ordered_cases(deficient, config, profile="full", concurrency=2)


def test_development_profile_selects_only_the_sequestered_pilot_partition() -> None:
    """The paid 40-case admission pilot must never consume the measured release partition."""
    config = small_config(load_config())
    development = config.profiles["development"].model_copy(update={"cases_count": 1})
    config = config.model_copy(update={"profiles": {**config.profiles, "development": development}})

    selected = ordered_cases(_artifact(config), config, profile="development", concurrency=32)

    assert tuple(case.case_id for case in selected) == ("dev-0",)


def test_binding_a_server_twice_accepts_reported_memory_jitter_but_not_a_different_machine() -> (
    None
):
    """Resuming an arm on another GPU would silently compare two populations as if they were one."""
    config, _artifact_unused, manifest = complete_benchmark(load_config())
    provenance = server(config, "bf16")
    empty = manifest.model_copy(update={"servers": {}})

    bound = bind_server(empty, provenance)

    assert bound.servers["bf16"] == provenance
    jittered = provenance.model_copy(
        update={"weight_memory_gib": provenance.weight_memory_gib * 1.001}
    )
    assert bind_server(bound, jittered).servers["bf16"] == provenance
    with pytest.raises(ValueError, match="provenance drifted"):
        bind_server(bound, provenance.model_copy(update={"gpu_name": "different"}))

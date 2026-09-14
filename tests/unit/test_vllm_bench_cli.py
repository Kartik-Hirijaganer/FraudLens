"""Summary: vLLM benchmark CLI dispatch, refusal, archive, and validation tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Every external action is mocked; release and GPU commands are never executed.
"""

from __future__ import annotations

import argparse
import gzip
import subprocess
from pathlib import Path

import pytest
from vllm_bench_fakes import HASH, benchmark_case, complete_benchmark, server

import benchmark_vllm
from lib.vllm_bench.config import load_config
from lib.vllm_bench.state import CaseArtifact, write_case_bundle, write_run


def test_main_dispatches_validate_cases_serve_stop_and_report(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(benchmark_vllm, "load_config", lambda _path: load_config())
    monkeypatch.setattr(benchmark_vllm, "_validate", lambda _config: calls.append("validate"))
    monkeypatch.setattr(
        benchmark_vllm,
        "_build_cases",
        lambda _config, profile, source: calls.append((profile, source)),
    )
    monkeypatch.setattr(benchmark_vllm, "serve", lambda _config, arm: calls.append(arm))
    monkeypatch.setattr(benchmark_vllm, "stop", lambda _config: calls.append("stop"))
    monkeypatch.setattr(
        benchmark_vllm,
        "_report",
        lambda _config, run_id, path: calls.append((run_id, path.name)),
    )
    assert benchmark_vllm.main(["validate"]) == 0
    assert benchmark_vllm.main(["cases", "--profile", "smoke", "--source", "sar-eval"]) == 0
    assert benchmark_vllm.main(["serve", "--arm", "bf16"]) == 0
    assert benchmark_vllm.main(["stop"]) == 0
    assert (
        benchmark_vllm.main(
            ["report", "--run", "vllm-bench-0123456789abcdef", "--cases", "cases.json"]
        )
        == 0
    )
    assert calls[0] == "validate"
    assert ("smoke", "sar-eval") in calls
    assert "bf16" in calls and "stop" in calls


@pytest.mark.asyncio
async def test_run_without_api_key_refuses_before_transport(sandbox, monkeypatch) -> None:
    config, artifact, _manifest = complete_benchmark(load_config())
    root = sandbox / config.paths.output_dir
    case_path = root / "cases-ibm-final-test-full.json"
    write_case_bundle(case_path, artifact)
    monkeypatch.setattr(benchmark_vllm, "REPO_ROOT", sandbox)
    monkeypatch.setattr(benchmark_vllm, "read_startup_logs", lambda *_args, **_kwargs: "logs")
    monkeypatch.setattr(benchmark_vllm, "image_digest", lambda _config: f"sha256:{'c' * 64}")
    monkeypatch.setattr(
        benchmark_vllm,
        "server_provenance",
        lambda *_args, **_kwargs: server(config, "bf16"),
    )
    monkeypatch.delenv(config.server.api_key_env, raising=False)
    args = argparse.Namespace(
        profile="full",
        source="ibm-final-test",
        arm="bf16",
        run="vllm-bench-0123456789abcdef",
        host=config.cost.default_host,
        purchase_option="pay_as_you_go",
    )
    with pytest.raises(ValueError, match="VLLM_API_KEY is required"):
        await benchmark_vllm._run(args, config)


def test_cases_release_is_hash_named_licensed_and_external_call_is_explicit(
    sandbox, monkeypatch
) -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    root = sandbox / config.paths.output_dir
    case_path = root / "cases-ibm-final-test-full.json"
    case_hash = write_case_bundle(case_path, artifact)
    run_path = root / manifest.run_id / "run.json"
    write_run(run_path, manifest.model_copy(update={"cases_sha256": case_hash}))
    calls = []
    monkeypatch.setattr(benchmark_vllm, "REPO_ROOT", sandbox)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: (
            calls.append((command, kwargs)) or subprocess.CompletedProcess(command, 0)
        ),
    )
    release = benchmark_vllm._release_cases(config, manifest.run_id, confirm_upload=True)
    archive = root / "releases" / release.archive_name
    assert release.license == "CDLA-Sharing-1.0"
    assert release.archive_name == f"vllm-cases-ibm-{case_hash[:16]}.jsonl.gz"
    assert len(gzip.decompress(archive.read_bytes()).splitlines()) == len(artifact.cases)
    assert calls[0][0][:3] == ("gh", "release", "upload")
    assert calls[0][0][3] == manifest.run_id


def test_release_refuses_ambiguous_or_non_full_cases(sandbox, monkeypatch) -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    root = sandbox / config.paths.output_dir
    case_path = root / "cases-a.json"
    case_hash = write_case_bundle(case_path, artifact)
    write_case_bundle(root / "cases-b.json", artifact)
    write_run(
        root / manifest.run_id / "run.json",
        manifest.model_copy(update={"cases_sha256": case_hash}),
    )
    monkeypatch.setattr(benchmark_vllm, "REPO_ROOT", sandbox)
    with pytest.raises(PermissionError, match="explicit"):
        benchmark_vllm._release_cases(config, manifest.run_id)
    with pytest.raises(ValueError, match="exactly one"):
        benchmark_vllm._release_cases(config, manifest.run_id, confirm_upload=True)


def test_validate_checks_optional_publication_pair(monkeypatch) -> None:
    config = load_config()
    artifact = complete_benchmark(config)[1]
    fixture = artifact.model_copy(update={"profile": "smoke", "case_source": "sar-eval"})
    monkeypatch.setattr(benchmark_vllm, "build_fixture_cases", lambda *_args, **_kwargs: fixture)
    monkeypatch.setattr(benchmark_vllm, "_DOCS_REPORT", Path("missing-a"))
    monkeypatch.setattr(benchmark_vllm, "_FRONTEND_REPORT", Path("missing-b"))
    with pytest.raises(ValueError, match="smoke case-set counts drifted"):
        benchmark_vllm._validate(config)


def test_case_builder_dispatches_both_sources_and_writes_stable_path(sandbox, monkeypatch) -> None:
    config, artifact, _manifest = complete_benchmark(load_config())
    monkeypatch.setattr(benchmark_vllm, "REPO_ROOT", sandbox)
    monkeypatch.setattr(
        benchmark_vllm,
        "build_fixture_cases",
        lambda *_args, **_kwargs: artifact.model_copy(
            update={"profile": "smoke", "case_source": "sar-eval"}
        ),
    )
    fixture_path = benchmark_vllm._build_cases(config, "smoke", "sar-eval")
    assert fixture_path.name == "cases-sar-eval-smoke.json"
    assert fixture_path.is_file()
    monkeypatch.setattr(benchmark_vllm, "build_ibm_cases", lambda *_args, **_kwargs: artifact)
    ibm_path = benchmark_vllm._build_cases(config, "full", "ibm-final-test")
    assert ibm_path.name == "cases-ibm-final-test-full.json"


@pytest.mark.asyncio
async def test_run_with_injected_endpoint_executes_and_closes_client(sandbox, monkeypatch) -> None:
    config, artifact, _manifest = complete_benchmark(load_config())
    root = sandbox / config.paths.output_dir
    write_case_bundle(root / "cases-ibm-final-test-full.json", artifact)
    monkeypatch.setattr(benchmark_vllm, "REPO_ROOT", sandbox)
    monkeypatch.setattr(benchmark_vllm, "read_startup_logs", lambda *_args, **_kwargs: "logs")
    monkeypatch.setattr(benchmark_vllm, "image_digest", lambda _config: f"sha256:{'c' * 64}")
    monkeypatch.setattr(
        benchmark_vllm,
        "server_provenance",
        lambda *_args, **_kwargs: server(config, "bf16"),
    )
    seen = {"closed": False, "run_id": ""}

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        async def close(self) -> None:
            seen["closed"] = True

    async def run_arm(**_kwargs):
        seen["run_id"] = _kwargs["run_id"]

    monkeypatch.setattr(benchmark_vllm, "OpenAiCompatibleStreamClient", Client)
    monkeypatch.setattr(benchmark_vllm, "run_arm", run_arm)
    monkeypatch.setattr(benchmark_vllm, "build_sampler", lambda _config: object())
    monkeypatch.setenv(config.server.api_key_env, "test-key")
    args = argparse.Namespace(
        profile="full",
        source="ibm-final-test",
        arm="bf16",
        run=None,
        host=config.cost.default_host,
        purchase_option="pay_as_you_go",
    )
    await benchmark_vllm._run(args, config)
    assert seen["closed"] is True
    assert str(seen["run_id"]).startswith("vllm-bench-")
    assert len(str(seen["run_id"])) == len("vllm-bench-") + 16


def test_report_builds_from_hash_bound_local_inputs(sandbox, monkeypatch) -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    root = sandbox / config.paths.output_dir
    case_path = root / "cases-ibm-final-test-full.json"
    case_hash = write_case_bundle(case_path, artifact)
    write_run(
        root / manifest.run_id / "run.json",
        manifest.model_copy(update={"cases_sha256": case_hash}),
    )
    monkeypatch.setattr(benchmark_vllm, "REPO_ROOT", sandbox)
    benchmark_vllm._report(config, manifest.run_id, case_path)
    assert (root / manifest.run_id / "report.json").is_file()


def test_validate_success_and_incomplete_published_pair(sandbox, monkeypatch) -> None:
    config = load_config()
    cases = (
        *(benchmark_case(f"case-{index}") for index in range(8)),
        benchmark_case("warmup", "warmup"),
        benchmark_case("abstention-0", "abstention"),
        benchmark_case("abstention-1", "abstention"),
    )
    fixture = CaseArtifact(
        protocol_version=config.protocol_version,
        profile="smoke",
        case_source="sar-eval",
        config_sha256=config.config_sha256,
        upstream_sha256=HASH,
        prompt_version="v1",
        prompt_sha256=HASH,
        distinct_subjects=8,
        subject_overlap=0,
        cases=cases,
    )
    monkeypatch.setattr(benchmark_vllm, "REPO_ROOT", sandbox)
    monkeypatch.setattr(benchmark_vllm, "build_fixture_cases", lambda *_args, **_kwargs: fixture)
    docs = sandbox / "report.json"
    frontend = sandbox / "frontend.json"
    monkeypatch.setattr(benchmark_vllm, "_DOCS_REPORT", docs)
    monkeypatch.setattr(benchmark_vllm, "_FRONTEND_REPORT", frontend)
    benchmark_vllm._validate(config)
    docs.write_text("present")
    with pytest.raises(ValueError, match="pair is incomplete"):
        benchmark_vllm._validate(config)

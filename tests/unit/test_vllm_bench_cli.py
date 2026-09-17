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
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from cascade_fakes import (
    ScriptedCascadeDrafter,
    budget_config,
    cascade_artifact,
    cascade_config,
    ledger_row,
    replay_manifest,
)
from vllm_bench_fakes import (
    HASH,
    FakeSampler,
    benchmark_case,
    complete_benchmark,
    server,
    small_config,
)

import benchmark_vllm
from fraudlens_backend.sar.budget import SarBudgetExceededError
from fraudlens_backend.sar.factory import load_sar_llm_config
from lib.vllm_bench import scenario_runtime
from lib.vllm_bench.config import load_config
from lib.vllm_bench.state import CaseArtifact, load_run, write_case_bundle, write_run


def test_main_dispatches_validate_cases_serve_stop_e2e_and_report(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(benchmark_vllm, "load_config", lambda _path: load_config())
    monkeypatch.setattr(benchmark_vllm, "_validate", lambda _config: calls.append("validate"))
    monkeypatch.setattr(
        benchmark_vllm,
        "_build_cases",
        lambda _config, profile, source: calls.append((profile, source)),
    )
    monkeypatch.setattr(
        benchmark_vllm,
        "serve",
        lambda _config, arm, **_kwargs: calls.append(arm),
    )
    monkeypatch.setattr(
        benchmark_vllm,
        "stop",
        lambda _config, **_kwargs: calls.append("stop"),
    )
    monkeypatch.setattr(
        benchmark_vllm,
        "_report",
        lambda _config, run_id, path: calls.append((run_id, path.name)),
    )
    monkeypatch.setattr(
        benchmark_vllm,
        "_e2e",
        lambda args, _config: calls.append(("e2e", args.cases, args.concurrency)),
    )
    assert benchmark_vllm.main(["validate"]) == 0
    assert benchmark_vllm.main(["cases", "--profile", "smoke", "--source", "sar-eval"]) == 0
    assert benchmark_vllm.main(["serve", "--arm", "bf16"]) == 0
    assert benchmark_vllm.main(["stop"]) == 0
    assert benchmark_vllm.main(["e2e", "--cases", "100", "--concurrency", "4"]) == 0
    assert (
        benchmark_vllm.main(
            ["report", "--run", "vllm-bench-0123456789abcdef", "--cases", "cases.json"]
        )
        == 0
    )
    assert calls[0] == "validate"
    assert ("smoke", "sar-eval") in calls
    assert ("e2e", 100, 4) in calls
    assert "bf16" in calls and "stop" in calls


def test_e2e_uses_configured_environment_indirection(sandbox, monkeypatch) -> None:
    config = load_config()
    client = object()
    calls: dict[str, object] = {}
    monkeypatch.setattr(benchmark_vllm, "REPO_ROOT", sandbox)
    monkeypatch.setenv(config.application_pass.base_url_env, "https://fraudlens.invalid")
    monkeypatch.setenv(config.application_pass.auth_token_env, "synthetic-token")
    monkeypatch.setattr(
        benchmark_vllm.httpx,
        "Client",
        lambda **kwargs: calls.update(http=kwargs) or nullcontext(client),
    )
    monkeypatch.setattr(
        benchmark_vllm,
        "run_e2e",
        lambda **kwargs: (
            calls.update(e2e=kwargs)
            or SimpleNamespace(
                runs_completed=1,
                requested_cases=1,
                llm_provider="vllm",
                runs_failed=0,
            )
        ),
    )

    benchmark_vllm._e2e(
        argparse.Namespace(
            run="vllm-e2e-0123456789abcdef",
            cases=1,
            concurrency=1,
            model_override=None,
        ),
        config,
    )

    assert config.application_pass.base_url == "http://127.0.0.1:18000"
    assert calls["http"] == {
        "base_url": "https://fraudlens.invalid",
        "headers": {"Authorization": "Bearer synthetic-token"},
        "timeout": 30.0,
    }
    assert calls["e2e"]["client"] is client  # type: ignore[index]


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


def test_cascade_pilot_writes_local_evidence_and_publishes_only_when_asked(
    sandbox, monkeypatch
) -> None:
    """The free pilot always leaves local evidence; publishing it stays an explicit choice."""
    config = cascade_config(small_config(load_config()))
    artifact, manifest = replay_manifest(config)
    root = sandbox / config.paths.output_dir
    case_path = root / "cases-ibm-final-test-full.json"
    case_hash = write_case_bundle(case_path, artifact)
    write_run(
        root / manifest.run_id / "run.json", manifest.model_copy(update={"cases_sha256": case_hash})
    )
    published = sandbox / "pilot.json"
    monkeypatch.setattr(benchmark_vllm, "REPO_ROOT", sandbox)
    monkeypatch.setattr(benchmark_vllm, "_DOCS_PILOT", published)
    monkeypatch.setattr(benchmark_vllm, "load_budget_config", lambda _root: budget_config())
    monkeypatch.setattr(benchmark_vllm, "load_ledger", lambda _path: ledger_row(manifest.run_id))
    monkeypatch.setattr(
        benchmark_vllm,
        "load_sar_llm_config",
        lambda path: load_sar_llm_config(Path("config") / config.cascade.sar_config_file),
    )
    arguments = argparse.Namespace(
        run=manifest.run_id, profile="awq-bf16", cases=case_path, publish=False
    )

    pilot = benchmark_vllm._cascade_pilot(arguments, config)

    assert (root / manifest.run_id / "cascade-pilot.json").is_file()
    assert not published.exists()
    assert pilot.run_id == manifest.run_id

    benchmark_vllm._cascade_pilot(
        argparse.Namespace(run=manifest.run_id, profile="awq-bf16", cases=case_path, publish=True),
        config,
    )
    assert published.is_file()


def test_cascade_pilot_refuses_a_run_bound_to_another_corpus(sandbox, monkeypatch) -> None:
    """A pilot judged against the wrong corpus would grade different cases than it measured."""
    config = cascade_config(small_config(load_config()))
    artifact, manifest = replay_manifest(config)
    root = sandbox / config.paths.output_dir
    case_path = root / "cases-ibm-final-test-full.json"
    write_case_bundle(case_path, artifact)
    write_run(
        root / manifest.run_id / "run.json", manifest.model_copy(update={"cases_sha256": HASH})
    )
    monkeypatch.setattr(benchmark_vllm, "REPO_ROOT", sandbox)
    arguments = argparse.Namespace(
        run=manifest.run_id, profile="awq-bf16", cases=case_path, publish=False
    )

    with pytest.raises(ValueError, match="does not bind the provided case artifact"):
        benchmark_vllm._cascade_pilot(arguments, config)


async def test_run_scenario_drives_the_production_drafter_over_both_endpoint_roles(
    sandbox, monkeypatch
) -> None:
    """The measured subject must be the shipped cascade, built from the scenario's SAR profile."""
    config = cascade_config(small_config(load_config()))
    artifact = cascade_artifact(config)
    root = sandbox / config.paths.output_dir
    write_case_bundle(root / "cases-ibm-final-test-full.json", artifact)
    startup_prefixes: list[tuple[str, ...]] = []
    drafter = ScriptedCascadeDrafter()

    monkeypatch.setattr(benchmark_vllm, "REPO_ROOT", sandbox)
    monkeypatch.setattr(benchmark_vllm, "build_scenario_drafter", lambda *_args: drafter)

    def _startup_logs(_config, *, repo_root, command_prefix=()):
        del repo_root
        startup_prefixes.append(tuple(command_prefix))
        return "logs"

    monkeypatch.setattr(benchmark_vllm, "read_startup_logs", _startup_logs)
    monkeypatch.setattr(benchmark_vllm, "image_digest", lambda _config: f"sha256:{'b' * 64}")
    monkeypatch.setattr(
        benchmark_vllm,
        "server_provenance",
        lambda _config, **kwargs: server(config, kwargs["arm"]),
    )
    monkeypatch.setattr(benchmark_vllm, "build_sampler", lambda _telemetry: FakeSampler())
    monkeypatch.setenv("FRAUDLENS_ENVIRONMENT", "prod")
    monkeypatch.setenv("VLLM_BF16_TELEMETRY_PREFIX", "ssh bf16-host --")
    monkeypatch.setenv("VLLM_BENCH_GIT_COMMIT", "a" * 40)
    arguments = argparse.Namespace(
        scenario="awq-bf16",
        profile="full",
        source="ibm-final-test",
        run="vllm-bench-0123456789abcdef",
        host=config.cost.default_host,
        purchase_option="pay_as_you_go",
        cases=root / "cases-ibm-final-test-full.json",
    )

    await benchmark_vllm._run_scenario(arguments, config)

    manifest = load_run(root / "vllm-bench-0123456789abcdef" / "run.json")
    assert startup_prefixes == [(), ("ssh", "bf16-host", "--")]
    assert manifest.git_commit == "a" * 40
    assert set(manifest.servers) == {"awq", "bf16"}
    assert list(manifest.levels) == [f"awq-bf16:{config.load.concurrency_levels[-1]}"]
    assert drafter.calls > 0


def test_scenario_runtime_binds_the_production_overlay_and_daily_budget(monkeypatch) -> None:
    """The live harness must use the production profile and BudgetGuard rather than bypassing it."""
    observed = {}

    def _drafter(settings, *, budget, client):
        observed.update(settings=settings, budget=budget, client=client)
        return "drafter"

    monkeypatch.setenv("FRAUDLENS_ENVIRONMENT", "prod")
    monkeypatch.setattr(scenario_runtime, "build_sar_drafter", _drafter)
    monkeypatch.setattr(scenario_runtime, "_scenario_client", object)

    result = scenario_runtime.build_scenario_drafter(load_config(), "awq-bf16")

    assert result == "drafter"
    assert observed["settings"].environment == "prod"
    assert observed["settings"].sar_profile == "awq-bf16"
    observed["budget"].record(Decimal("22.00"))
    with pytest.raises(SarBudgetExceededError, match="daily budget"):
        observed["budget"].ensure_within_budget()

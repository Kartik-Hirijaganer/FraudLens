"""Behavioral tests for explicit SAR evaluation CLI stages and provider separation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn

import pytest

import benchmark_sar_agents as cli
from lib.sar_eval.config import DEFAULT_SAR_EVAL_CONFIG, load_sar_eval_config

_RUN_ID = "sar-eval-0123456789abcdef"


def _bomb(*_args: object, **_kwargs: object) -> NoReturn:
    raise AssertionError("provider/API client must not be instantiated in this stage")


def _config_copy(tmp_path: Path) -> Path:
    target = tmp_path / "sar-eval.yaml"
    target.write_bytes(DEFAULT_SAR_EVAL_CONFIG.read_bytes())
    return target


def test_scenarios_stage_is_free_deterministic_and_instantiates_no_clients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("benchmark_sar_agents.httpx.Client", _bomb)
    monkeypatch.setattr("benchmark_sar_agents.LlmClient.from_settings", _bomb)

    assert cli.main(["--config", str(_config_copy(tmp_path)), "scenarios"]) == 0
    outputs = list((tmp_path / ".local" / "sar-eval").glob("*/scenarios.json"))
    assert len(outputs) == 1
    assert '"scenarios"' in outputs[0].read_text(encoding="utf-8")


def test_publish_stage_is_local_only_and_run_requires_explicit_auth_and_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _config_copy(tmp_path)
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("benchmark_sar_agents.httpx.Client", _bomb)
    monkeypatch.setattr("benchmark_sar_agents.LlmClient.from_settings", _bomb)
    monkeypatch.setattr(cli, "load_scenarios", lambda _path: SimpleNamespace())
    monkeypatch.setattr(cli, "load_api_runs", lambda _path: SimpleNamespace())
    monkeypatch.setattr(cli, "load_judgments", lambda _path: SimpleNamespace())
    monkeypatch.setattr(
        cli,
        "load_corpus",
        lambda _path: (SimpleNamespace(citation="31 U.S.C. 5318(g)"),),
    )
    monkeypatch.setattr(cli, "build_study_report", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(
        cli,
        "publish_report",
        lambda *_args, **_kwargs: SimpleNamespace(report_sha256="a" * 64),
    )
    monkeypatch.delenv("SAR_EVAL_BASE_URL", raising=False)
    monkeypatch.delenv("SAR_EVAL_AUTH_TOKEN", raising=False)

    assert cli.main(["--config", str(config_path), "publish", "--run", _RUN_ID]) == 0
    assert cli.main(["--config", str(config_path), "run", "--run", _RUN_ID]) == 1


def test_validate_stage_rechecks_real_committed_paths_without_clients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, Path]] = []
    bindings: list[object] = []
    report = object()
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("benchmark_sar_agents.httpx.Client", _bomb)
    monkeypatch.setattr("benchmark_sar_agents.LlmClient.from_settings", _bomb)

    def validate(report_path: Path, frontend: Path) -> object:
        calls.append((report_path, frontend))
        return report

    monkeypatch.setattr(
        cli,
        "validate_published_artifacts",
        validate,
    )
    monkeypatch.setattr(
        cli,
        "validate_report_binding",
        lambda observed, _config: bindings.append(observed),
    )

    assert cli.main(["validate"]) == 0
    assert calls == [
        (
            tmp_path / "docs/reference/benchmarks/sar-multi-agent-study.json",
            tmp_path / "frontend/src/data/sar-multi-agent-study.json",
        )
    ]
    assert bindings == [report]


def test_run_stage_alone_constructs_authenticated_api_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    class _ApiContext:
        def __init__(self, **kwargs: object) -> None:
            calls["http"] = kwargs

        def __enter__(self) -> object:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("benchmark_sar_agents.httpx.Client", _ApiContext)
    monkeypatch.setattr("benchmark_sar_agents.LlmClient.from_settings", _bomb)
    monkeypatch.setattr(cli, "load_scenarios", lambda _path: SimpleNamespace())
    monkeypatch.setattr(
        cli,
        "run_api_stage",
        lambda *_args, **kwargs: calls.update(stage=kwargs) or SimpleNamespace(spent_usd="0.01"),
    )
    monkeypatch.setattr(cli, "write_api_runs", lambda path, _artifact: calls.update(path=path))
    monkeypatch.setenv("SAR_EVAL_BASE_URL", "https://fraudlens.invalid")
    monkeypatch.setenv("SAR_EVAL_AUTH_TOKEN", "synthetic-test-token")
    monkeypatch.setenv("SAR_EVAL_RUN_MAX_USD", "1.00")

    result = cli.main(
        [
            "--config",
            str(_config_copy(tmp_path)),
            "run",
            "--run",
            _RUN_ID,
            "--retry-failed",
        ]
    )

    assert result == 0
    assert calls["http"] == {
        "base_url": "https://fraudlens.invalid",
        "headers": {"Authorization": "Bearer synthetic-test-token"},
        "timeout": 30.0,
    }
    assert calls["path"] == tmp_path / f".local/sar-eval/{_RUN_ID}/runs.json"
    assert calls["stage"]["retry_failed"] is True  # type: ignore[index]


def test_judge_stage_alone_constructs_provider_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    provider = object()
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("benchmark_sar_agents.httpx.Client", _bomb)
    monkeypatch.setattr(
        "benchmark_sar_agents.LlmClient.from_settings",
        lambda: calls.update(provider=True) or provider,
    )
    monkeypatch.setattr(cli, "load_scenarios", lambda _path: SimpleNamespace())
    monkeypatch.setattr(cli, "load_api_runs", lambda _path: SimpleNamespace())
    monkeypatch.setattr(cli, "get_llm_settings", lambda: SimpleNamespace(catalog_path="catalog"))
    monkeypatch.setattr(cli, "load_catalog", lambda _path: SimpleNamespace())
    monkeypatch.setattr(
        "benchmark_sar_agents.JudgePromptTemplate.load",
        lambda _prompt_id: SimpleNamespace(),
    )

    async def _judge(*_args: object, **kwargs: object) -> SimpleNamespace:
        calls["judge"] = kwargs
        return SimpleNamespace(spent_usd="0.01")

    monkeypatch.setattr(cli, "run_judge_stage", _judge)
    monkeypatch.setattr(cli, "write_judgments", lambda path, _artifact: calls.update(path=path))
    monkeypatch.setenv("SAR_EVAL_JUDGE_MAX_USD", "1.00")

    result = cli.main(["--config", str(_config_copy(tmp_path)), "judge", "--run", _RUN_ID])

    assert result == 0
    assert calls["provider"] is True
    assert calls["judge"]["client"] is provider  # type: ignore[index]
    assert calls["path"] == tmp_path / f".local/sar-eval/{_RUN_ID}/judgments.json"


@pytest.mark.parametrize("value", [None, "", "zero", "0", "NaN", "Infinity", "-1"])
def test_spending_cap_parser_requires_a_finite_positive_decimal(value: str | None) -> None:
    with pytest.raises(ValueError, match=r"positive decimal|required"):
        cli._positive_decimal(value, "SAR_EVAL_TEST_MAX_USD")


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("https://fraudlens.invalid", True),
        ("http://localhost:8000", True),
        ("http://127.0.0.1:8000/", True),
        ("http://fraudlens.invalid", False),
        ("https://token@fraudlens.invalid", False),
        ("https://fraudlens.invalid/api", False),
        ("not-a-url", False),
    ],
)
def test_base_url_protects_bearer_token_destination(value: str, accepted: bool) -> None:
    loopback_http_hosts = load_sar_eval_config().api.loopback_http_hosts
    if accepted:
        assert cli._validated_base_url(
            value, loopback_http_hosts=loopback_http_hosts
        ) == value.rstrip("/")
    else:
        with pytest.raises(ValueError, match="SAR_EVAL_BASE_URL"):
            cli._validated_base_url(value, loopback_http_hosts=loopback_http_hosts)


def test_run_directory_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="canonical sar-eval identifier"):
        cli._run_dir(_config_copy(tmp_path), "../../outside")

"""Full-data CLI stage-dispatch, pilot, estimate, publish, and validation tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fulldata_fakes import fulldata_config

import fulldata
from lib.fulldata.cost import load_cost_projection


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[object, list[str]]:
    """Patch every stage with observable providerless fakes."""
    config = fulldata_config(tmp_path)
    budget = fulldata.load_budget_config(Path.cwd())
    calls: list[str] = []
    monkeypatch.setattr(fulldata, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(fulldata, "load_fulldata_config", lambda _path: config)
    monkeypatch.setattr(fulldata, "load_budget_config", lambda _root: budget)
    monkeypatch.setattr(fulldata, "_print_model", lambda _value: calls.append("print"))
    for name in (
        "verify_dataset",
        "ingest_dataset",
        "build_features",
        "validate_feature_parity",
        "build_folds",
        "train_candidate",
        "evaluate_run",
        "validate_published_artifacts",
        "publish_report",
    ):
        monkeypatch.setattr(
            fulldata,
            name,
            lambda *_args, _name=name, **_kwargs: calls.append(_name) or config.datasets[0],
        )
    report = SimpleNamespace(run_id="run-one")
    monkeypatch.setattr(
        fulldata, "write_report", lambda *_args: calls.append("write_report") or report
    )
    monkeypatch.setattr(fulldata, "current_run_id", lambda *_args: "run-one")
    monkeypatch.setattr(fulldata, "run_directory", lambda *_args: tmp_path / "run-one")
    return config, calls


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["verify", "--candidate", "hi-small"], "verify_dataset"),
        (["ingest", "--candidate", "hi-small", "--rows", "1000"], "ingest_dataset"),
        (["features", "--candidate", "hi-small"], "build_features"),
        (["parity", "--candidate", "hi-small", "--sample-rows", "2"], "validate_feature_parity"),
        (["folds", "--candidate", "hi-small"], "build_folds"),
        (["train", "--candidate", "hi-small"], "train_candidate"),
        (["evaluate"], "evaluate_run"),
        (["report"], "write_report"),
        (["validate"], "validate_published_artifacts"),
        (["publish"], "publish_report"),
    ],
)
def test_cli_dispatches_each_stage(
    cli: tuple[object, list[str]], argv: list[str], expected: str
) -> None:
    _config, calls = cli
    assert fulldata.main(argv) == 0
    assert expected in calls


def test_cli_verify_all_and_pilot_run_expected_stages(
    cli: tuple[object, list[str]],
) -> None:
    _config, calls = cli
    assert fulldata.main(["verify"]) == 0
    assert calls.count("verify_dataset") == 1
    calls.clear()
    assert fulldata.main(["pilot", "--candidate", "hi-small", "--rows", "1000"]) == 0
    assert [call for call in calls if call != "print"][:5] == [
        "ingest_dataset",
        "build_features",
        "validate_feature_parity",
        "build_folds",
        "train_candidate",
    ]
    with pytest.raises(ValueError, match="pilot rows"):
        fulldata.main(["pilot", "--candidate", "hi-small", "--rows", "7"])


def test_cli_estimate_uses_budget_rate(cli: tuple[object, list[str]]) -> None:
    config, calls = cli
    assert (
        fulldata.main(
            [
                "estimate",
                "--rate",
                "azure_e16ads_v5_spot",
                "--pilot-hours",
                "1",
                "--pilot-rows",
                "1000",
                "--target-rows",
                "2000",
            ]
        )
        == 0
    )
    assert "print" in calls
    projection = load_cost_projection(config, fulldata.REPO_ROOT)
    assert projection is not None
    assert projection.allocation == "azure_cpu_batch"
    assert projection.projection.projected_cost_usd > 0

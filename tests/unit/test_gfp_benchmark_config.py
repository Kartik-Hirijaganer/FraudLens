"""GFP benchmark-config tests (GFP study plan Phase 2). The committed
config/gfp-benchmark.yaml must parse into the frozen `GfpBenchmarkConfig` with exactly the
contract's pins, every dataset source must resolve in the fetch registry, and the model must
REJECT bad windows/bins/fractions/quotas/paths/engine-versions plus protocol drift (edge-column
changes, quota mismatches, duplicate datasets). Also guards the dependency boundary: snapml
lives only in the root benchmark-only `gfp` group with the x86-64 marker."""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from fetch_dataset import IBM_AML, IBM_AML_HI_MEDIUM, IBM_AML_LI_MEDIUM, dataset_spec
from lib.gfp.config import (
    CANONICAL_EDGE_COLUMNS,
    DEFAULT_GFP_BENCHMARK_CONFIG,
    GfpBenchmarkConfig,
    load_gfp_benchmark_config,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _payload() -> dict[str, Any]:
    """A fresh, mutable copy of the committed pin file's raw mapping."""
    loaded = yaml.safe_load(DEFAULT_GFP_BENCHMARK_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return copy.deepcopy(loaded)


def _expect_invalid(payload: dict[str, Any]) -> None:
    """Assert the mutated payload is rejected by the model."""
    with pytest.raises(ValidationError):
        GfpBenchmarkConfig.model_validate(payload)


def test_committed_config_matches_the_frozen_contract() -> None:
    """The committed YAML carries exactly the plan's 'Benchmark contract' pins."""
    config = load_gfp_benchmark_config()
    assert config.seed == 1729
    assert config.batch_size == 128
    assert config.engine.name == "snapml"
    assert config.engine.version == "1.17.2"
    assert config.edge_columns == CANONICAL_EDGE_COLUMNS
    windows = config.windows
    assert windows.scatter_gather_window_s == 21600
    for family_window in (
        windows.global_window_s,
        windows.fan_window_s,
        windows.degree_window_s,
        windows.vertex_stats_window_s,
        windows.temporal_cycle_window_s,
        windows.simple_cycle_window_s,
    ):
        assert family_window == 86400
    for wide in (
        config.bins.fan,
        config.bins.degree,
        config.bins.scatter_gather,
        config.bins.temporal_cycle,
    ):
        assert (wide.lo, wide.hi) == (2, 30)
    assert (config.bins.simple_cycle.lo, config.bins.simple_cycle.hi) == (2, 10)
    assert config.simple_cycle_max_length == 10
    assert config.vertex_stats.endpoints == ("source_out", "source_in", "target_out", "target_in")
    assert config.vertex_stats.stats == (
        "fan",
        "degree",
        "ratio",
        "avg",
        "sum",
        "var",
        "skew",
        "kurtosis",
    )
    assert config.vertex_stats.raw_columns == ("utc_epoch_s", "usd_amount")
    assert config.sampling.node_hash_fractions == ("1/4", "1/3", "1/2")
    assert (config.folds.train, config.folds.calibration, config.folds.holdout) == (
        "3/5",
        "1/5",
        "1/5",
    )
    assert (
        config.target_quotas.train,
        config.target_quotas.calibration,
        config.target_quotas.holdout,
    ) == (600000, 200000, 200000)
    assert config.target_quotas.total == 1000000
    assert config.paths.data_dir == ".local/aml_data"
    assert config.paths.output_dir == ".local/gfp-study"
    # The 15 AMLworld currency names are pinned with USD == 1 (Phase 3 edge builder).
    assert len(config.usd_rates) == 15
    assert config.usd_rates["us dollar"] == "1"
    assert "bitcoin" in config.usd_rates


def test_rejects_bad_usd_rates() -> None:
    for mutate in (
        {"us dollar": "0"},  # non-positive rate
        {"euro": "one-point-one"},  # not a decimal
        {"": "1.0"},  # blank currency name
        {},  # empty table
    ):
        payload = _payload()
        payload["usd_rates"] = mutate
        _expect_invalid(payload)
    duplicate_after_normalization = _payload()
    duplicate_after_normalization["usd_rates"] = {"Euro": "1.10", "euro ": "1.20"}
    _expect_invalid(duplicate_after_normalization)


def test_datasets_pin_contexts_and_resolve_in_fetch_registry() -> None:
    """HI-Small is full-context; the Medium variants are node-induced with the 1M quota; every
    source id resolves in scripts/fetch_dataset.py's registry (no phantom datasets)."""
    config = load_gfp_benchmark_config()
    by_source = {dataset.source: dataset for dataset in config.datasets}
    assert set(by_source) == {IBM_AML, IBM_AML_HI_MEDIUM, IBM_AML_LI_MEDIUM}
    assert by_source[IBM_AML].graph_context == "full"
    assert by_source[IBM_AML].target_quota is None
    for medium in (IBM_AML_HI_MEDIUM, IBM_AML_LI_MEDIUM):
        assert by_source[medium].graph_context == "node_induced"
        assert by_source[medium].target_quota == 1000000
    for source in by_source:
        dataset_spec(source)  # raises KeyError if the registry does not know the id


def test_model_is_frozen_and_rejects_unknown_keys() -> None:
    config = load_gfp_benchmark_config()
    with pytest.raises(ValidationError):
        config.seed = 1  # type: ignore[misc]
    extra_root = _payload()
    extra_root["surprise"] = 1
    _expect_invalid(extra_root)
    extra_nested = _payload()
    extra_nested["windows"]["bonus_window_s"] = 60
    _expect_invalid(extra_nested)


def test_rejects_nonpositive_windows() -> None:
    for bad in (0, -86400):
        payload = _payload()
        payload["windows"]["fan_window_s"] = bad
        _expect_invalid(payload)


def test_rejects_bad_bin_ranges() -> None:
    below_minimum = _payload()
    below_minimum["bins"]["degree"] = {"lo": 1, "hi": 30}
    _expect_invalid(below_minimum)
    empty_range = _payload()
    empty_range["bins"]["fan"] = {"lo": 5, "hi": 5}
    _expect_invalid(empty_range)
    beyond_cycle_cap = _payload()
    beyond_cycle_cap["bins"]["simple_cycle"] = {"lo": 2, "hi": 12}  # cap stays 10
    _expect_invalid(beyond_cycle_cap)


def test_rejects_bad_hash_fraction_ladders() -> None:
    for bad_ladder in (
        ["0/4", "1/3", "1/2"],  # zero fraction
        ["1/4", "1/3", "3/2"],  # above 1
        ["1/3", "1/4", "1/2"],  # not strictly increasing
        ["1/4", "1/4", "1/2"],  # repeated step
        ["quarter"],  # not a rational
        [],  # empty ladder
    ):
        payload = _payload()
        payload["sampling"]["node_hash_fractions"] = bad_ladder
        _expect_invalid(payload)


def test_rejects_bad_fold_fractions() -> None:
    not_summing = _payload()
    not_summing["folds"] = {"train": "1/2", "calibration": "1/4", "holdout": "1/8"}
    _expect_invalid(not_summing)
    whole_fold = _payload()
    whole_fold["folds"] = {"train": "1/1", "calibration": "1/5", "holdout": "1/5"}
    _expect_invalid(whole_fold)
    malformed = _payload()
    malformed["folds"] = {"train": "most", "calibration": "1/5", "holdout": "1/5"}
    _expect_invalid(malformed)


def test_rejects_bad_quotas() -> None:
    zero_quota = _payload()
    zero_quota["target_quotas"]["calibration"] = 0
    _expect_invalid(zero_quota)
    mismatched = _payload()
    mismatched["datasets"][1]["target_quota"] = 999999  # != 600k + 200k + 200k
    _expect_invalid(mismatched)
    full_with_quota = _payload()
    full_with_quota["datasets"][0]["target_quota"] = 1000000
    _expect_invalid(full_with_quota)
    sampled_without_quota = _payload()
    sampled_without_quota["datasets"][2]["target_quota"] = None
    _expect_invalid(sampled_without_quota)


def test_rejects_bad_paths() -> None:
    for field, bad_path in (
        ("output_dir", "/tmp/gfp-study"),  # absolute
        ("output_dir", ".local/../docs/gfp"),  # upward traversal
        ("data_dir", "docs/reference"),  # outside .local/
        ("output_dir", ".local"),  # the bare scratch root is too broad
        ("data_dir", "~/aml_data"),  # home-relative
    ):
        payload = _payload()
        payload["paths"][field] = bad_path
        _expect_invalid(payload)


def test_rejects_bad_engine_pins() -> None:
    below_floor = _payload()
    below_floor["engine"]["version"] = "1.14"
    _expect_invalid(below_floor)
    malformed = _payload()
    malformed["engine"]["version"] = "one.seventeen"
    _expect_invalid(malformed)
    wrong_engine = _payload()
    wrong_engine["engine"]["name"] = "networkx"
    _expect_invalid(wrong_engine)


def test_rejects_edge_column_drift() -> None:
    reordered = _payload()
    reordered["edge_columns"] = ["dense_src", "edge_id", "dense_dst", "utc_epoch_s", "usd_amount"]
    _expect_invalid(reordered)
    missing = _payload()
    missing["edge_columns"] = ["edge_id", "dense_src", "dense_dst", "utc_epoch_s"]
    _expect_invalid(missing)
    renamed = _payload()
    renamed["edge_columns"] = ["edge_id", "src", "dst", "utc_epoch_s", "usd_amount"]
    _expect_invalid(renamed)


def test_rejects_duplicate_dataset_sources() -> None:
    payload = _payload()
    payload["datasets"][2]["source"] = payload["datasets"][1]["source"]
    _expect_invalid(payload)


def test_rejects_bad_seed_and_batch_size() -> None:
    negative_seed = _payload()
    negative_seed["seed"] = -1
    _expect_invalid(negative_seed)
    zero_batch = _payload()
    zero_batch["batch_size"] = 0
    _expect_invalid(zero_batch)


def test_rejects_bad_vertex_stats() -> None:
    duplicated = _payload()
    duplicated["vertex_stats"]["stats"] = ["fan", "fan"]
    _expect_invalid(duplicated)
    unknown_column = _payload()
    unknown_column["vertex_stats"]["raw_columns"] = ["utc_epoch_s", "raw_account_token"]
    _expect_invalid(unknown_column)
    unknown_endpoint = _payload()
    unknown_endpoint["vertex_stats"]["endpoints"] = ["source_out", "sideways"]
    _expect_invalid(unknown_endpoint)


def test_loader_rejects_non_mapping_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "gfp.yaml"
    bad.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_gfp_benchmark_config(bad)


def test_gfp_dependency_group_is_isolated() -> None:
    """snapml lives ONLY in the root `gfp` group, exact-pinned with the x86-64 marker, and
    never appears in a workspace member or the default groups (ADR-017 dependency boundary)."""
    root = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    group = root["dependency-groups"]["gfp"]
    assert group == ["snapml==1.17.2 ; platform_machine == 'x86_64'"]
    assert "gfp" not in root["tool"]["uv"].get("default-groups", [])
    for member in (
        "backend/pyproject.toml",
        "packages/fraudlens-core/pyproject.toml",
        "packages/fraudlens-llm/pyproject.toml",
        "packages/fraudlens-ml/pyproject.toml",
    ):
        assert "snapml" not in (_REPO_ROOT / member).read_text(encoding="utf-8")
    # The YAML engine pin and the dependency-group pin can never drift apart.
    assert f"snapml=={load_gfp_benchmark_config().engine.version}" in group[0]

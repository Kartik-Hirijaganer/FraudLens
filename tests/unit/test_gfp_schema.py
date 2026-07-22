"""GFP schema tests (GFP plan Phase 3): feature names are GENERATED from the validated
config (no hand-maintained lists), in the empirically pinned snapml output order; arm
B/C groupings partition the set; and the serving guard holds — the generated names are
disjoint from the 19 served FEATURE_NAMES and 'gfp_'-prefixed."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fraudlens_ml.scoring.features import FEATURE_NAMES
from lib.gfp.config import GfpBinRange, load_gfp_benchmark_config
from lib.gfp.schema import (
    GRAPH_ARM_B_FEATURE_NAMES,
    GRAPH_ARM_C_INCREMENT_FEATURE_NAMES,
    GRAPH_FEATURE_NAMES,
    GraphFeatureConfig,
    GraphFeatureSchema,
    histogram_cell_names,
)

_CANONICAL_CONFIG = GraphFeatureConfig.from_benchmark(load_gfp_benchmark_config())
_SCHEMA = GraphFeatureSchema.from_config(_CANONICAL_CONFIG)

# Empirically pinned block layout for the committed protocol (snapml 1.17.2 probes):
# fan_in 0..28, fan_out 29..57, degree_in 58..86, degree_out 87..115, scatter_gather
# 116..144, temporal_cycle 145..173, simple_cycle 174..182, vertex stats 183..234.
_EXPECTED_TOTAL = 235


def test_histogram_cell_names_format() -> None:
    names = histogram_cell_names("fan_in", GfpBinRange(lo=2, hi=4))
    assert names == ("gfp_fan_in_ge_2_lt_3", "gfp_fan_in_ge_3_lt_4", "gfp_fan_in_ge_4")


def test_committed_schema_matches_the_pinned_snapml_layout() -> None:
    names = _SCHEMA.feature_names
    assert len(names) == _EXPECTED_TOTAL
    assert names[0] == "gfp_fan_in_ge_2_lt_3"
    assert names[28] == "gfp_fan_in_ge_30"
    assert names[29] == "gfp_fan_out_ge_2_lt_3"
    assert names[58] == "gfp_degree_in_ge_2_lt_3"
    assert names[87] == "gfp_degree_out_ge_2_lt_3"
    assert names[116] == "gfp_scatter_gather_ge_2_lt_3"
    assert names[145] == "gfp_temporal_cycle_ge_2_lt_3"
    assert names[174] == "gfp_simple_cycle_ge_2_lt_3"
    assert names[182] == "gfp_simple_cycle_ge_10"
    # Vertex stats: 4 endpoints x (3 structural + 2 raw columns x 5 moments) = 52.
    assert names[183] == "gfp_source_out_fan"
    assert names[186] == "gfp_source_out_utc_epoch_s_avg"
    assert names[191] == "gfp_source_out_usd_amount_avg"
    assert names[196] == "gfp_source_in_fan"
    assert names[222] == "gfp_target_in_fan"
    assert names[234] == "gfp_target_in_usd_amount_kurtosis"


def test_arm_groupings_partition_the_features() -> None:
    assert set(GRAPH_ARM_B_FEATURE_NAMES) | set(GRAPH_ARM_C_INCREMENT_FEATURE_NAMES) == set(
        GRAPH_FEATURE_NAMES
    )
    assert not set(GRAPH_ARM_B_FEATURE_NAMES) & set(GRAPH_ARM_C_INCREMENT_FEATURE_NAMES)
    # B = fan + degree histograms + vertex stats; C = the three multi-hop families.
    assert len(GRAPH_ARM_B_FEATURE_NAMES) == 29 * 4 + 52
    assert len(GRAPH_ARM_C_INCREMENT_FEATURE_NAMES) == 29 * 2 + 9
    assert all(name.startswith("gfp_") for name in GRAPH_FEATURE_NAMES)


def test_serving_guard_names_disjoint_from_served_features() -> None:
    assert set(GRAPH_FEATURE_NAMES).isdisjoint(FEATURE_NAMES)
    assert len(FEATURE_NAMES) == 19  # the served vector must stay exactly 19 wide


def test_column_indices_project_names() -> None:
    indices = _SCHEMA.column_indices(_SCHEMA.arm_c_increment_names)
    assert indices[0] == 116  # scatter-gather block starts after the arm-B histograms
    assert len(indices) == len(_SCHEMA.arm_c_increment_names)


def test_schema_rejects_bad_partitions() -> None:
    with pytest.raises(ValidationError):
        GraphFeatureSchema(
            feature_names=("gfp_a", "gfp_b"),
            family_names={"fan_in": ("gfp_a", "gfp_b")},
            arm_b_names=("gfp_a",),
            arm_c_increment_names=("gfp_a",),  # overlap + not a partition
        )
    with pytest.raises(ValidationError):
        GraphFeatureSchema(
            feature_names=("gfp_a", "gfp_a"),  # duplicate names
            family_names={},
            arm_b_names=("gfp_a",),
            arm_c_increment_names=(),
        )


def test_schema_tracks_config_changes() -> None:
    narrowed = _CANONICAL_CONFIG.model_copy(update={"fan_bins": GfpBinRange(lo=2, hi=5)})
    schema = GraphFeatureSchema.from_config(narrowed)
    assert len(schema.family_names["fan_in"]) == 4
    assert schema.feature_names[3] == "gfp_fan_in_ge_5"
    assert len(schema.feature_names) == _EXPECTED_TOTAL - 2 * (29 - 4)

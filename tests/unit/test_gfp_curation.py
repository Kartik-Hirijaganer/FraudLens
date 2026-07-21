"""GFP visual-curation tests (GFP plan Phase 6): the three typology exemplars are
found in a graph that really contains them (via REFERENCE-engine signals), the
cross-tenant cycle disappears from every per-tenant graph (positive feature delta on
exactly its edges), redaction emits only opaque ids / relative offsets / amount bands,
a typology with no candidate is reported missing (never invented), and the ranking is
deterministic with illicit-content priority over feature delta."""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from lib.gfp.config import load_gfp_benchmark_config
from lib.gfp.curation import (
    CROSS_TENANT_CYCLE,
    INTRA_TENANT_CYCLE,
    SCATTER_GATHER,
    TYPOLOGIES,
    CurationSignals,
    curate_motifs,
    curation_signals,
)
from lib.gfp.edges import build_gfp_edge_set
from lib.gfp.materialize import materialize_scope_features
from lib.gfp.reference import ReferenceGraphPreprocessor
from lib.gfp.schema import GraphFeatureConfig, GraphFeatureSchema

_CONFIG = load_gfp_benchmark_config()
_FEATURE_CONFIG = GraphFeatureConfig.from_benchmark(_CONFIG)
_SCHEMA = GraphFeatureSchema.from_config(_FEATURE_CONFIG)
_AGENCIES = 3  # banks "0"/"1"/"2" map onto agencies 0/1/2 via demo_agency_index


def _row(
    minute: int, src: tuple[str, str], dst: tuple[str, str], amount: str, label: str = "0"
) -> dict[str, str]:
    return {
        "Timestamp": f"2022/09/01 00:{minute:02d}",
        "From Bank": src[0],
        "Account": src[1],
        "To Bank": dst[0],
        "Account.1": dst[1],
        "Amount Paid": amount,
        "Payment Currency": "US Dollar",
        "Is Laundering": label,
    }


# One graph carrying all three typologies: a scatter-gather A -> {M1, M2} -> B (with an
# illicit member edge), an intra-tenant cycle X1 -> X2 -> X3 -> X1 (all agency 0), and a
# CROSS-tenant cycle Y1 -> Y2 -> Y3 -> Y1 whose edges are owned by agencies 0/1/2.
_MOTIF_ROWS: tuple[dict[str, str], ...] = (
    _row(0, ("0", "A"), ("0", "M1"), "500.00"),
    _row(1, ("0", "A"), ("0", "M2"), "5000.00", label="1"),
    _row(2, ("0", "M1"), ("0", "B"), "50.00"),
    _row(3, ("0", "M2"), ("0", "B"), "20000.00"),
    _row(10, ("0", "X1"), ("0", "X2"), "200000.00"),
    _row(11, ("0", "X2"), ("0", "X3"), "300.00"),
    _row(12, ("0", "X3"), ("0", "X1"), "300.00"),
    _row(20, ("0", "Y1"), ("1", "Y2"), "300.00"),
    _row(21, ("1", "Y2"), ("2", "Y3"), "300.00"),
    _row(22, ("2", "Y3"), ("0", "Y1"), "300.00"),
)
_CROSS_ROWS = (7, 8, 9)  # edge rows of the cross-tenant cycle in sorted edge order


def _curated(rows: tuple[dict[str, str], ...] = _MOTIF_ROWS) -> tuple:
    edges = build_gfp_edge_set(pd.DataFrame(rows, dtype=str), _CONFIG, agency_count=_AGENCIES)
    factory = lambda: ReferenceGraphPreprocessor(_FEATURE_CONFIG)  # noqa: E731
    global_features = materialize_scope_features(
        edges, factory, scope="global", batch_size=_CONFIG.batch_size, agency_count=_AGENCIES
    )
    tenant_features = materialize_scope_features(
        edges, factory, scope="per_tenant", batch_size=_CONFIG.batch_size, agency_count=_AGENCIES
    )
    signals = curation_signals(_SCHEMA, global_features, tenant_features)
    return edges, signals, curate_motifs(edges, _FEATURE_CONFIG, signals)


def test_all_three_typologies_are_found_and_redacted() -> None:
    _, signals, result = _curated()
    assert result.missing_typologies == ()
    assert tuple(motif.typology for motif in result.motifs) == TYPOLOGIES
    by_typology = {motif.typology: motif for motif in result.motifs}

    scatter = by_typology[SCATTER_GATHER]
    assert len(scatter.nodes) == 4  # A, M1, M2, B
    assert len(scatter.edges) == 4
    assert scatter.servable is True  # every edge owned by agency 0
    assert [edge.amount_band for edge in scatter.edges] == [
        "100-1k",
        "1k-10k",
        "lt-100",
        "10k-100k",
    ]
    assert [edge.time_offset_s for edge in scatter.edges] == [0, 60, 120, 180]

    intra = by_typology[INTRA_TENANT_CYCLE]
    assert len(intra.edges) == 3
    assert intra.servable is True
    assert {edge.owner_agency_index for edge in intra.edges} == {0}
    assert intra.edges[0].amount_band == "ge-100k"

    cross = by_typology[CROSS_TENANT_CYCLE]
    assert len(cross.edges) == 3
    assert cross.servable is False
    assert {edge.owner_agency_index for edge in cross.edges} == {0, 1, 2}
    # The cross-tenant cycle vanishes from every single-agency graph. Under the pinned
    # batch-causal semantics the cycle count lands on the member edges inside the
    # CLOSING batch (rows 8-9; row 7 sits in an earlier fold batch), so those rows
    # carry the positive global-vs-tenant feature delta.
    assert signals.feature_delta[list(_CROSS_ROWS[1:])].min() > 0
    assert signals.feature_delta[list(_CROSS_ROWS)].sum() > 0

    for motif in result.motifs:
        assert motif.motif_id.startswith(motif.typology)
        assert all(node.node_id.startswith("node-") for node in motif.nodes)
        assert all(edge.edge_id.startswith("edge-") for edge in motif.edges)
        assert min(edge.time_offset_s for edge in motif.edges) == 0


def test_curation_is_deterministic() -> None:
    _, _, first = _curated()
    _, _, second = _curated()
    assert first.motifs == second.motifs
    assert first.missing_typologies == second.missing_typologies


def test_missing_cross_tenant_cycle_is_reported_not_invented() -> None:
    _, _, result = _curated(_MOTIF_ROWS[:7])  # drop the Y-cycle rows entirely
    assert result.missing_typologies == (CROSS_TENANT_CYCLE,)
    assert tuple(motif.typology for motif in result.motifs) == (
        SCATTER_GATHER,
        INTRA_TENANT_CYCLE,
    )


def test_zero_activity_reports_every_typology_missing() -> None:
    edges = build_gfp_edge_set(
        pd.DataFrame(_MOTIF_ROWS, dtype=str), _CONFIG, agency_count=_AGENCIES
    )
    n = edges.gfp_matrix.shape[0]
    silent = CurationSignals(
        scatter_activity=np.zeros(n), cycle_activity=np.zeros(n), feature_delta=np.zeros(n)
    )
    result = curate_motifs(edges, _FEATURE_CONFIG, silent)
    assert result.motifs == ()
    assert result.missing_typologies == TYPOLOGIES
    with pytest.raises(ValueError, match="align 1:1"):
        curate_motifs(
            edges,
            _FEATURE_CONFIG,
            CurationSignals(
                scatter_activity=np.zeros(n - 1),
                cycle_activity=np.zeros(n),
                feature_delta=np.zeros(n),
            ),
        )


def test_ranking_prefers_illicit_members_then_feature_delta() -> None:
    # Two disjoint scatter-gathers: SG1 (rows 0-3) has an illicit member edge; SG2
    # (rows 4-7) is fully licit. Handcrafted signals give SG2 a larger delta.
    rows = (
        _row(0, ("0", "A"), ("0", "M1"), "500.00"),
        _row(1, ("0", "A"), ("0", "M2"), "500.00", label="1"),
        _row(2, ("0", "M1"), ("0", "B"), "500.00"),
        _row(3, ("0", "M2"), ("0", "B"), "500.00"),
        _row(10, ("0", "A2"), ("0", "M3"), "500.00"),
        _row(11, ("0", "A2"), ("0", "M4"), "500.00"),
        _row(12, ("0", "M3"), ("0", "B2"), "500.00"),
        _row(13, ("0", "M4"), ("0", "B2"), "500.00"),
    )
    edges = build_gfp_edge_set(pd.DataFrame(rows, dtype=str), _CONFIG, agency_count=_AGENCIES)
    n = edges.gfp_matrix.shape[0]
    delta = np.zeros(n)
    delta[4:] = 100.0  # SG2 dominates on delta, but SG1 carries the illicit edge
    signals = CurationSignals(
        scatter_activity=np.ones(n), cycle_activity=np.zeros(n), feature_delta=delta
    )
    result = curate_motifs(edges, _FEATURE_CONFIG, signals)
    scatter = next(m for m in result.motifs if m.typology == SCATTER_GATHER)
    assert len(scatter.nodes) == 4
    # SG1 wins on the illicit-content primary key despite the smaller delta.
    winner_offsets = [edge.time_offset_s for edge in scatter.edges]
    assert winner_offsets == [0, 60, 120, 180]
    assert scatter.motif_id == curate_motifs(edges, _FEATURE_CONFIG, signals).motifs[0].motif_id

    # Relabel every row licit: the feature delta becomes the deciding key and SG2 wins.
    relabeled = dataclasses.replace(edges, labels=np.zeros(n, dtype=np.int8))
    delta_winner = next(
        m
        for m in curate_motifs(relabeled, _FEATURE_CONFIG, signals).motifs
        if m.typology == SCATTER_GATHER
    )
    assert delta_winner.motif_id != scatter.motif_id


def test_inconsistent_node_ownership_fails_loudly() -> None:
    edges = build_gfp_edge_set(
        pd.DataFrame(_MOTIF_ROWS[:4], dtype=str), _CONFIG, agency_count=_AGENCIES
    )
    corrupted = dataclasses.replace(
        edges, source_agency=np.array([0, 1, 0, 0], dtype=np.int16)
    )  # node A is agency 0 on row 0 but agency 1 on row 1
    signals = CurationSignals(
        scatter_activity=np.ones(4), cycle_activity=np.zeros(4), feature_delta=np.zeros(4)
    )
    with pytest.raises(ValueError, match="ownership is inconsistent"):
        curate_motifs(corrupted, _FEATURE_CONFIG, signals)


def test_curation_signals_reject_misaligned_scopes() -> None:
    with pytest.raises(ValueError, match="must align"):
        curation_signals(_SCHEMA, np.zeros((3, 4)), np.zeros((2, 4)))

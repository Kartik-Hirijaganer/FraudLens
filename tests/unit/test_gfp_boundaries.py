"""GFP boundary/report model tests (GFP plan Phase 3): the frozen Pydantic records
reject inconsistent counts, undeclared motif nodes, cross-owner 'servable' motifs,
unscoped arms, inverted intervals, and undocumented null retained-lift; artifacts
serialize with camelCase aliases; and servingEligible can never be true."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lib.gfp.boundaries import (
    CuratedMotif,
    CuratedMotifEdge,
    CuratedMotifNode,
    CuratedVisualData,
    DatasetProvenance,
    DatasetStudySpec,
    FoldAssignment,
    StudyHighlightMetrics,
)
from lib.gfp.report import (
    ArmDelta,
    ArmMetrics,
    HoldoutSummary,
    PairedDeltaInterval,
    ScopeComparison,
    StudyReport,
    TopKMetrics,
)

_FOLDS = FoldAssignment(
    fractions=("3/5", "1/5", "1/5"),
    boundary_epochs_s=(100, 200),
    fold_sizes=(6, 2, 2),
    fold_positive_counts=(1, 1, 1),
)
_SPEC = DatasetStudySpec(
    source="ibm-aml", variant="HI-Small_Trans.csv", graph_context="full", target_quota=None
)


def _provenance(**overrides: object) -> DatasetProvenance:
    payload: dict[str, object] = {
        "spec": _SPEC,
        "file_sha256": "0" * 64,
        "source_row_count": 100,
        "servable_row_count": 90,
        "context_edge_count": 90,
        "target_count": 10,
        "node_hash_fraction": None,
        "illicit_ratio": 0.1,
        "fold_assignment": _FOLDS,
        "fold_target_counts": (6, 2, 2),
        "fold_target_positive_counts": (1, 1, 1),
    }
    payload.update(overrides)
    return DatasetProvenance(**payload)  # type: ignore[arg-type]


def test_fold_assignment_rejects_impossible_positives() -> None:
    with pytest.raises(ValidationError, match="exceed size"):
        FoldAssignment(
            fractions=("3/5", "1/5", "1/5"),
            boundary_epochs_s=(1, 2),
            fold_sizes=(2, 2, 2),
            fold_positive_counts=(3, 0, 0),
        )


def test_provenance_cross_checks() -> None:
    assert _provenance().target_count == 10
    with pytest.raises(ValidationError, match="context cannot exceed"):
        _provenance(context_edge_count=95, servable_row_count=90)
    with pytest.raises(ValidationError, match="targets cannot exceed"):
        _provenance(target_count=95)
    with pytest.raises(ValidationError, match="sum to target_count"):
        _provenance(fold_target_counts=(1, 1, 1))
    with pytest.raises(ValidationError, match="must record the hash fraction"):
        _provenance(
            spec=_SPEC.model_copy(update={"graph_context": "node_induced", "target_quota": 10})
        )
    with pytest.raises(ValidationError, match="must not record"):
        _provenance(node_hash_fraction="1/4")


def test_provenance_serializes_camel_case() -> None:
    dumped = _provenance().model_dump(by_alias=True)
    assert "servableRowCount" in dumped
    assert "foldTargetCounts" in dumped
    assert dumped["spec"]["graphContext"] == "full"


def _motif(*, owners: tuple[int, int] = (0, 0), servable: bool = True) -> CuratedMotif:
    nodes = (
        CuratedMotifNode(node_id="node-01", agency_index=owners[0]),
        CuratedMotifNode(node_id="node-02", agency_index=owners[1]),
    )
    edges = (
        CuratedMotifEdge(
            edge_id="edge-01",
            source_node_id="node-01",
            target_node_id="node-02",
            time_offset_s=0,
            amount_band="1k-10k",
            owner_agency_index=owners[0],
        ),
        CuratedMotifEdge(
            edge_id="edge-02",
            source_node_id="node-02",
            target_node_id="node-01",
            time_offset_s=3600,
            amount_band="1k-10k",
            owner_agency_index=owners[1],
        ),
    )
    return CuratedMotif(
        motif_id="m-1",
        typology="intra_tenant_cycle",
        nodes=nodes,
        edges=edges,
        servable=servable,
    )


def test_curated_motif_validates_nodes_and_servability() -> None:
    assert _motif().servable is True
    with pytest.raises(ValidationError, match="never be servable"):
        _motif(owners=(0, 1), servable=True)
    _motif(owners=(0, 1), servable=False)  # cross-tenant motifs are fine when unservable
    with pytest.raises(ValidationError, match="undeclared node"):
        CuratedMotif(
            motif_id="m-2",
            typology="scatter_gather",
            nodes=(CuratedMotifNode(node_id="node-01", agency_index=0),) * 2,
            edges=(
                CuratedMotifEdge(
                    edge_id="edge-01",
                    source_node_id="node-01",
                    target_node_id="node-09",
                    time_offset_s=0,
                    amount_band="<1k",
                    owner_agency_index=0,
                ),
            ),
            servable=True,
        )
    with pytest.raises(ValidationError):  # opaque id shape is enforced
        CuratedMotifNode(node_id="acct-8000EBD30", agency_index=0)


_HIGHLIGHT = StudyHighlightMetrics(
    dataset_source="ibm-aml",
    arm_a_pr_auc=0.20,
    arm_c_pr_auc=0.25,
    arm_c_pr_auc_normalized=25.0,
    arm_a_to_c_lift=0.05,
    arm_a_to_c_ci_lower=0.01,
    arm_a_to_c_ci_upper=0.07,
    isolation_delta_c=0.02,
)


def test_curated_visual_data_binds_a_report_hash() -> None:
    names = ("Agency One", "Agency Two")
    data = CuratedVisualData(
        report_sha256="a" * 64, metrics=_HIGHLIGHT, agency_names=names, motifs=(_motif(),)
    )
    dumped = data.model_dump(by_alias=True)
    assert dumped["reportSha256"] == "a" * 64
    assert dumped["agencyNames"] == names
    assert dumped["metrics"]["isolationDeltaC"] == 0.02
    assert dumped["metrics"]["armCPrAuc"] == 0.25
    assert dumped["motifs"][0]["edges"][0]["amountBand"] == "1k-10k"
    with pytest.raises(ValidationError, match="beyond the 1 declared agency names"):
        CuratedVisualData(
            report_sha256="a" * 64,
            metrics=_HIGHLIGHT,
            agency_names=("Agency One",),
            motifs=(_motif(owners=(0, 1), servable=False),),
        )


def test_highlight_metrics_reject_inverted_interval() -> None:
    with pytest.raises(ValidationError, match="lower bound exceeds its upper"):
        StudyHighlightMetrics(
            dataset_source="ibm-aml",
            arm_a_pr_auc=0.20,
            arm_c_pr_auc=0.25,
            arm_c_pr_auc_normalized=25.0,
            arm_a_to_c_lift=0.05,
            arm_a_to_c_ci_lower=0.09,
            arm_a_to_c_ci_upper=0.01,
            isolation_delta_c=0.02,
        )


def _arm_metrics(arm: str, scope: str) -> ArmMetrics:
    return ArmMetrics(
        dataset_source="ibm-aml",
        arm=arm,  # type: ignore[arg-type]
        scope=scope,  # type: ignore[arg-type]
        holdout=HoldoutSummary(positives=10, negatives=990, illicit_ratio=0.01),
        pr_auc=0.5,
        pr_auc_normalized=50.0,
        roc_auc=0.9,
        brier=0.01,
        ece=0.02,
        top_k=(TopKMetrics(fraction=0.001, precision=0.5, recall=0.1, captured_positives=1),),
        minority_f1=0.4,
        minority_f1_threshold=0.7,
    )


def test_arm_metrics_scope_rules() -> None:
    assert _arm_metrics("A", "shared").arm == "A"
    assert _arm_metrics("B", "global").scope == "global"
    with pytest.raises(ValidationError, match="must be 'shared'"):
        _arm_metrics("A", "global")
    with pytest.raises(ValidationError, match="must be scoped"):
        _arm_metrics("C", "shared")


def test_interval_and_scope_comparison_rules() -> None:
    with pytest.raises(ValidationError, match="exceeds upper"):
        PairedDeltaInterval(lower=0.2, upper=0.1, replicates=200, holdout_subset_cap=250000)
    good = PairedDeltaInterval(lower=-0.01, upper=0.02, replicates=200, holdout_subset_cap=250000)
    ArmDelta(
        dataset_source="ibm-aml",
        from_arm="A",
        to_arm="C",
        scope="global",
        pr_auc_delta=0.01,
        interval=good,
    )
    with pytest.raises(ValidationError, match="requires retained_lift_note"):
        ScopeComparison(
            dataset_source="ibm-aml",
            isolation_delta_b=0.0,
            isolation_delta_c=-0.01,
            lost_graph_lift=0.0,
            retained_graph_lift=None,
            retained_lift_note=None,
        )
    with pytest.raises(ValidationError, match="only for the null-denominator"):
        ScopeComparison(
            dataset_source="ibm-aml",
            isolation_delta_b=0.1,
            isolation_delta_c=0.1,
            lost_graph_lift=0.05,
            retained_graph_lift=0.5,
            retained_lift_note="unnecessary note",
        )
    negative_delta = ScopeComparison(
        dataset_source="ibm-aml",
        isolation_delta_b=-0.02,  # signed: a negative delta is a VALID result
        isolation_delta_c=-0.01,
        lost_graph_lift=-0.01,
        retained_graph_lift=None,
        retained_lift_note="global lift was not positive; retained lift undefined",
    )
    assert negative_delta.isolation_delta_b < 0


def test_study_report_is_never_serving_eligible() -> None:
    report = StudyReport(
        run_id="run-1",
        engine_name="snapml",
        engine_version="1.17.2",
        library_versions={"snapml": "1.17.2"},
        config_sha256="b" * 64,
        seed=1729,
        datasets=(_provenance(),),
        arm_feature_counts={"A": 19, "B": 187, "C": 254},
        graph_feature_names=("gfp_fan_in_ge_2_lt_3", "gfp_scatter_gather_ge_2_lt_3"),
        metrics=(_arm_metrics("A", "shared"),),
        deltas=(),
        comparisons=(),
        serving_eligible=False,
        notes=("GFP batch transform is batch-causal, not row-at-a-time serving parity.",),
    )
    assert report.model_dump(by_alias=True)["servingEligible"] is False
    with pytest.raises(ValidationError):
        StudyReport.model_validate({**report.model_dump(), "serving_eligible": True})

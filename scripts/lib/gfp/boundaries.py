"""Summary: Frozen Pydantic boundary models for the offline GFP tenant-isolation study
(GFP plan Phase 3; serving boundary: ADR-017). These types carry the study's PHI-free
records between pipeline stages and into the published artifacts: what dataset was used
(`DatasetStudySpec`), what was actually sampled and how (`DatasetProvenance`), how the
chronological folds fell (`FoldAssignment`), and the opaque curated motif records the
research page renders (`CuratedMotif*`). Serialized artifacts use camelCase aliases
(FraudLens casing rule: camelCase surface, snake_case internals). Raw account tokens,
bank ids, file paths, and labels never appear on any of these models.

Key classes:
- DatasetStudySpec: one dataset's resolved study inputs (source, variant, context, quota).
- FoldAssignment: the frozen chronological fold record (boundaries, sizes, class counts).
- DatasetProvenance: file + sampling provenance (hash fraction, counts, ratios) per dataset.
- CuratedMotifNode: one opaque node of a curated motif (id + owning agency index).
- CuratedMotifEdge: one opaque curated edge (relative time offset, amount band, owner).
- CuratedMotif: a curated typology exemplar (nodes, edges, typology, servability).
- StudyHighlightMetrics: the few signed headline numbers the research page's hero renders.
- CuratedVisualData: the committed frontend payload (motifs + headline metrics + report hash).

Key functions:
- (none)

Notes:
- Everything is frozen + extra="forbid": a stage cannot smuggle undeclared fields into a
  committed artifact, and records cannot be mutated after they are derived.
- CuratedMotifEdge carries an amount BAND and a RELATIVE time offset only — never raw
  amounts, timestamps, account tokens, or labels (plan Phase 6 redaction contract).
- FoldAssignment fold ids are 0=train, 1=calibration, 2=holdout everywhere in this
  package (`lib.gfp.folds` produces them; samplers and scopes consume them).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

_ARTIFACT_MODEL_CONFIG = ConfigDict(
    frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True
)

_FOLD_NAMES: tuple[str, ...] = ("train", "calibration", "holdout")


class DatasetStudySpec(BaseModel):
    """One dataset's resolved study inputs, binding the fetch registry to the protocol."""

    model_config = _ARTIFACT_MODEL_CONFIG

    source: str = Field(..., min_length=1, description="Fetch-registry source id.")
    variant: str = Field(..., min_length=1, description="Dataset file name (e.g. CSV variant).")
    graph_context: Literal["full", "node_induced"] = Field(
        ..., description="Whether GFP sees every servable row or a node-induced subgraph."
    )
    target_quota: int | None = Field(
        default=None, gt=0, description="Stratified target cap; None = all servable rows."
    )


class FoldAssignment(BaseModel):
    """The frozen chronological fold record: cohort-safe boundaries + per-fold makeup."""

    model_config = _ARTIFACT_MODEL_CONFIG

    fractions: tuple[str, str, str] = Field(
        ..., description="Configured train/calibration/holdout fractions (exact rationals)."
    )
    boundary_epochs_s: tuple[int, int] = Field(
        ...,
        description=(
            "UTC epoch seconds of the LAST train row and LAST calibration row; equal-timestamp "
            "cohorts never span a boundary."
        ),
    )
    fold_sizes: tuple[int, int, int] = Field(
        ..., description="Row counts per fold (train, calibration, holdout)."
    )
    fold_positive_counts: tuple[int, int, int] = Field(
        ..., description="Illicit-label row counts per fold (train, calibration, holdout)."
    )

    @model_validator(mode="after")
    def _positives_fit(self) -> FoldAssignment:
        """Per-fold positives can never exceed the fold size."""
        for name, size, positives in zip(
            _FOLD_NAMES, self.fold_sizes, self.fold_positive_counts, strict=True
        ):
            if positives > size:
                raise ValueError(f"fold '{name}': positives {positives} exceed size {size}")
        return self


class DatasetProvenance(BaseModel):
    """PHI-free file + sampling provenance for one dataset in one study run."""

    model_config = _ARTIFACT_MODEL_CONFIG

    spec: DatasetStudySpec = Field(..., description="The resolved study inputs this run used.")
    file_sha256: str = Field(
        ..., min_length=64, max_length=64, description="SHA-256 of the source CSV bytes."
    )
    source_row_count: int = Field(..., ge=0, description="Raw data rows in the source CSV.")
    servable_row_count: int = Field(
        ..., ge=0, description="Rows surviving servable-frame normalization."
    )
    context_edge_count: int = Field(
        ..., ge=0, description="Edges in the retained graph context (== servable for full)."
    )
    target_count: int = Field(
        ..., ge=0, description="Stratified target rows (features are computed on ALL context)."
    )
    node_hash_fraction: str | None = Field(
        default=None,
        description="Node-keep hash fraction actually used (None for full-context datasets).",
    )
    illicit_ratio: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Illicit share of the retained context rows."
    )
    fold_assignment: FoldAssignment = Field(
        ..., description="The frozen chronological fold record for the retained context."
    )
    fold_target_counts: tuple[int, int, int] = Field(
        ..., description="Stratified target rows per fold (train, calibration, holdout)."
    )
    fold_target_positive_counts: tuple[int, int, int] = Field(
        ..., description="Illicit target rows per fold (train, calibration, holdout)."
    )

    @model_validator(mode="after")
    def _counts_consistent(self) -> DatasetProvenance:
        """Sampling can only shrink counts; per-fold targets must sum to the target count."""
        if self.context_edge_count > self.servable_row_count:
            raise ValueError("context cannot exceed the servable row count")
        if self.target_count > self.context_edge_count:
            raise ValueError("targets cannot exceed the retained context")
        if sum(self.fold_target_counts) != self.target_count:
            raise ValueError("per-fold target counts must sum to target_count")
        if self.spec.graph_context == "node_induced" and self.node_hash_fraction is None:
            raise ValueError("node_induced provenance must record the hash fraction used")
        if self.spec.graph_context == "full" and self.node_hash_fraction is not None:
            raise ValueError("full-context provenance must not record a hash fraction")
        return self


class CuratedMotifNode(BaseModel):
    """One opaque node of a curated motif (no account tokens — ever)."""

    model_config = _ARTIFACT_MODEL_CONFIG

    node_id: str = Field(
        ..., pattern=r"^node-\d{2,}$", description="Opaque sequential id, e.g. 'node-01'."
    )
    agency_index: int = Field(..., ge=0, description="Owning demo-agency index of the node.")


class CuratedMotifEdge(BaseModel):
    """One opaque curated edge: banded amount + relative offset, never raw values."""

    model_config = _ARTIFACT_MODEL_CONFIG

    edge_id: str = Field(
        ..., pattern=r"^edge-\d{2,}$", description="Opaque sequential id, e.g. 'edge-01'."
    )
    source_node_id: str = Field(..., pattern=r"^node-\d{2,}$", description="Source node id.")
    target_node_id: str = Field(..., pattern=r"^node-\d{2,}$", description="Target node id.")
    time_offset_s: int = Field(
        ..., ge=0, description="Seconds after the motif's earliest edge (relative time only)."
    )
    amount_band: str = Field(
        ..., min_length=1, description="Coarse USD band label (e.g. '1k-10k'), never a raw amount."
    )
    owner_agency_index: int = Field(
        ..., ge=0, description="Owning agency of the edge (= its source node's agency)."
    )


class CuratedMotif(BaseModel):
    """A curated typology exemplar rendered by the research page."""

    model_config = _ARTIFACT_MODEL_CONFIG

    motif_id: str = Field(..., min_length=1, description="Stable id of this motif.")
    typology: Literal["scatter_gather", "intra_tenant_cycle", "cross_tenant_cycle"] = Field(
        ..., description="Which laundering typology this motif exemplifies."
    )
    nodes: tuple[CuratedMotifNode, ...] = Field(
        ..., min_length=2, description="Opaque motif nodes."
    )
    edges: tuple[CuratedMotifEdge, ...] = Field(
        ..., min_length=1, description="Opaque motif edges."
    )
    servable: bool = Field(
        ...,
        description="True only when every displayed edge is owned by ONE tenant (plan Phase 6).",
    )

    @model_validator(mode="after")
    def _edges_reference_nodes(self) -> CuratedMotif:
        """Every edge endpoint must be a declared node; servability must match ownership."""
        known = {node.node_id for node in self.nodes}
        owners = {edge.owner_agency_index for edge in self.edges}
        for edge in self.edges:
            if edge.source_node_id not in known or edge.target_node_id not in known:
                raise ValueError(f"edge '{edge.edge_id}' references an undeclared node")
        if self.servable and len(owners) > 1:
            raise ValueError("a motif spanning multiple owners can never be servable")
        return self


class StudyHighlightMetrics(BaseModel):
    """The signed headline numbers the research page's hero renders (plan Phase 7).

    The metrics live in the StudyReport (`docs/reference/benchmarks/…json`), which the
    frontend build cannot import (it is outside the frontend package). Publication copies
    exactly these few values onto the one committed frontend artifact so the hero renders
    from a single build-time import. They are the full-data global scope's headline figures
    for the dataset the report's resume sentence is derived from; the sign of
    `isolation_delta_c` drives the "isolation delta" vs "cost of isolation" copy.
    """

    model_config = _ARTIFACT_MODEL_CONFIG

    dataset_source: str = Field(
        ..., min_length=1, description="Fetch-registry source id these headline metrics are from."
    )
    arm_a_pr_auc: float = Field(
        ..., ge=0.0, le=1.0, description="Arm A (19 served features) raw holdout PR-AUC."
    )
    arm_c_pr_auc: float = Field(
        ..., ge=0.0, le=1.0, description="Arm C (global) raw holdout PR-AUC — the hero PR-AUC."
    )
    arm_c_pr_auc_normalized: float = Field(
        ..., ge=0.0, description="Arm C (global) base-rate-normalized mean-lift PR-AUC."
    )
    arm_a_to_c_lift: float = Field(
        ..., description="A->C global PR-AUC delta (the full graph-feature lift)."
    )
    arm_a_to_c_ci_lower: float = Field(
        ..., description="Lower bound of the A->C paired 95% bootstrap interval."
    )
    arm_a_to_c_ci_upper: float = Field(
        ..., description="Upper bound of the A->C paired 95% bootstrap interval."
    )
    isolation_delta_c: float = Field(
        ...,
        description="Arm C signed isolation delta (global - per-tenant PR-AUC); sign drives copy.",
    )

    @model_validator(mode="after")
    def _interval_ordered(self) -> StudyHighlightMetrics:
        """The reported interval can never be inverted."""
        if self.arm_a_to_c_ci_lower > self.arm_a_to_c_ci_upper:
            raise ValueError("A->C interval lower bound exceeds its upper bound")
        return self


class CuratedVisualData(BaseModel):
    """The committed frontend payload: curated motifs bound to one published report."""

    model_config = _ARTIFACT_MODEL_CONFIG

    report_sha256: str = Field(
        ...,
        min_length=64,
        max_length=64,
        description="SHA-256 of the published study-report JSON these motifs were curated from.",
    )
    metrics: StudyHighlightMetrics = Field(
        ..., description="The signed headline metrics the research page's hero renders."
    )
    agency_names: tuple[str, ...] = Field(
        ...,
        min_length=1,
        description=(
            "Display names of the demo agencies, indexed by agency index (plan Phase 6: the "
            "emitted JSON carries agency index AND name; names are synthetic demo labels)."
        ),
    )
    motifs: tuple[CuratedMotif, ...] = Field(
        ..., min_length=1, description="The curated typology exemplars."
    )

    @model_validator(mode="after")
    def _agency_indices_resolve(self) -> CuratedVisualData:
        """Every node/edge agency index must resolve to a declared agency name."""
        count = len(self.agency_names)
        for motif in self.motifs:
            indices = {node.agency_index for node in motif.nodes} | {
                edge.owner_agency_index for edge in motif.edges
            }
            out_of_range = sorted(index for index in indices if index >= count)
            if out_of_range:
                raise ValueError(
                    f"motif '{motif.motif_id}' references agency indices {out_of_range} "
                    f"beyond the {count} declared agency names"
                )
        return self

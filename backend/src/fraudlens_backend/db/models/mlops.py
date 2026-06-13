"""Summary: The model-lifecycle (MLOps) tables (plan §9.2). Most are **platform** tables
(one shared model registry, no `agency_id`, CI-allowlisted): `training_datasets`,
`model_training_runs`, `model_versions` (the registry), `model_evaluations`,
`model_deployments` (the single active/canary pointer), and `drift_reports`. Two are
**tenant-scoped**: `training_labels` (labels from reviewed decisions) and
`model_inference_logs` (hash-only, no PHI). This split realizes the tenant-safe global
training policy (ADR-015): models are global while labels/inference stay per-tenant, and
no artifact, metric, dataset, or inference log carries a tenant identifier or raw PHI.

Key classes:
- TrainingLabel: a tenant-scoped outcome label from a matured reviewed decision.
- TrainingDataset: an immutable, content-hashed training dataset manifest (no PHI).
- ModelTrainingRun: one training run (trigger/params/metrics/artifact).
- ModelVersion: a registry entry with a lifecycle status (candidate→active→archived).
- ModelEvaluation: a candidate-vs-baseline evaluation with a pass/fail gate.
- ModelDeployment: the single live active/canary pointer (+ previous, for rollback).
- ModelInferenceLog: a tenant-scoped, hash-only inference record.
- DriftReport: an advisory drift report for a model version.

Key functions:
- (none)

Notes:
- `model_deployments` is a single-row pointer; promotion/rollback updates it in place so
  running processes reload on pointer change with no redeploy (plan §10.5).
- `model_evaluations.metrics` records overall AND per-tenant-slice metrics so a candidate
  cannot regress on any one agency (ADR-015); the gate logic lands in Phase 10.
- `model_inference_logs` stores only a `feature_hash` + probability — never PHI (§9.2).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from fraudlens_backend.db.base import (
    JSONB_TYPE,
    AgencyScopedMixin,
    Base,
    CreatedAtMixin,
    IdMixin,
    JsonValue,
    TimestampMixin,
    UpdatedAtMixin,
    str_enum,
)
from fraudlens_backend.db.models.enums import (
    JobStatus,
    LabelSource,
    ModelTrigger,
    ModelVersionStatus,
    Severity,
    TrainingLabelType,
)


class TrainingLabel(AgencyScopedMixin, CreatedAtMixin, Base):
    """A tenant-scoped outcome label sourced from a matured reviewed decision (§9.2)."""

    __tablename__ = "training_labels"
    __table_args__ = (Index("ix_training_labels_agency_id", "agency_id"),)

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("transactions.id"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("analysis_runs.id"), nullable=False)
    label: Mapped[TrainingLabelType] = mapped_column(str_enum(TrainingLabelType), nullable=False)
    source: Mapped[LabelSource] = mapped_column(
        str_enum(LabelSource), nullable=False, default=LabelSource.ANALYST_REVIEW
    )
    matured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)


class TrainingDataset(IdMixin, CreatedAtMixin, Base):
    """An immutable, content-hashed training dataset manifest — no PHI (ADR-015)."""

    __tablename__ = "training_datasets"

    snapshot_query: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    label_window: Mapped[str] = mapped_column(String(64), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    feature_spec: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ModelTrainingRun(IdMixin, TimestampMixin, Base):
    """One model training run (trigger/params/metrics/artifact, plan §9.2)."""

    __tablename__ = "model_training_runs"

    trigger: Mapped[ModelTrigger] = mapped_column(str_enum(ModelTrigger), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("training_datasets.id"), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus), nullable=False, default=JobStatus.PENDING
    )
    params: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    metrics: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    artifact_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )


class ModelVersion(IdMixin, CreatedAtMixin, Base):
    """A registry entry with a lifecycle status (candidate→active→archived, §9.2)."""

    __tablename__ = "model_versions"

    version_label: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    training_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("model_training_runs.id"), nullable=False
    )
    artifact_uri: Mapped[str] = mapped_column(String(512), nullable=False)
    feature_spec: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    metrics: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    status: Mapped[ModelVersionStatus] = mapped_column(
        str_enum(ModelVersionStatus),
        nullable=False,
        default=ModelVersionStatus.CANDIDATE,
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ModelEvaluation(IdMixin, CreatedAtMixin, Base):
    """A candidate-vs-baseline evaluation with a pass/fail gate (plan §9.2 / ADR-015)."""

    __tablename__ = "model_evaluations"

    model_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("model_versions.id"), nullable=False
    )
    baseline_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("model_versions.id"), nullable=True
    )
    metrics: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ModelDeployment(IdMixin, UpdatedAtMixin, Base):
    """The single live active/canary pointer (+ previous active, for rollback)."""

    __tablename__ = "model_deployments"

    active_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("model_versions.id"), nullable=False
    )
    canary_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("model_versions.id"), nullable=True
    )
    canary_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    previous_active_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("model_versions.id"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )


class ModelInferenceLog(AgencyScopedMixin, CreatedAtMixin, Base):
    """A tenant-scoped, hash-only inference record — never PHI (plan §9.2)."""

    __tablename__ = "model_inference_logs"
    __table_args__ = (Index("ix_model_inference_logs_agency_id", "agency_id"),)

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("analysis_runs.id"), nullable=False)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("model_versions.id"), nullable=False
    )
    was_canary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fraud_probability: Mapped[float] = mapped_column(Float, nullable=False)
    feature_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class DriftReport(IdMixin, CreatedAtMixin, Base):
    """An advisory drift report for a model version (PSI/feature drift, plan §9.2)."""

    __tablename__ = "drift_reports"

    model_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("model_versions.id"), nullable=False
    )
    window: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    severity: Mapped[Severity] = mapped_column(str_enum(Severity), nullable=False)
    advisory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

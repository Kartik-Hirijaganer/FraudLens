"""Summary: Pydantic request/response models for the admin model-lifecycle API (plan §5.3, §16
Phase 10 — endpoints 19-26). Every model is a `CamelModel` (camelCase wire, snake_case Python,
`extra="forbid"`), so a retrain trigger, a canary rollout, a rollback, the live deployment pointer,
the training-run history, and advisory drift reports all serialize PHI-free by construction —
feature/version labels, numeric metrics, counts, and enum statuses only (no PHI, no raw identifiers,
ADR-015). `status`/`severity` reuse the canonical ORM enums (no duplicated vocabulary, rule 5);
`CanaryRequest.percent` is a `Literal` so only the documented 5/25/50/100 ramp is accepted (422
otherwise).

Key classes:
- TriggerTrainingRequest: the (optional) retrain trigger body — manual | scheduled.
- TrainingRunTriggerResponse: the 202 acknowledgement of a submitted retrain Job + label counts.
- TrainingRunView: one model training run projected onto the API surface.
- TrainingRunListResponse: the training-run history.
- CanaryRequest: the canary ramp percent (5 | 25 | 50 | 100).
- DeploymentResponse: the live active/canary pointer projected onto the API surface.
- RollbackResponse: what a rollback did + the resulting deployment.
- CanaryEvaluationResponse: the canary auto-abort verdict + per-arm inference stats.
- DriftReportView: one advisory drift report projected onto the API surface.
- DriftReportListResponse: advisory drift reports.

Key functions:
- (none)

Notes:
- Deployment/version ids are surfaced as version LABELS (human-readable, stable) rather than UUIDs
  where it aids the operator; both are PHI-free.
- `TrainingRunTriggerResponse.jobId` is the submitted Job id (the actual training runs out-of-band
  as a Container Apps Job / `make retrain`); the candidate appears once that Job completes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from fraudlens_backend.db.models.enums import Severity
from fraudlens_backend.models.common import CamelModel


class TriggerTrainingRequest(CamelModel):
    """The optional retrain trigger body (defaults to a manual run)."""

    trigger: Literal["manual", "scheduled"] = Field(
        default="manual", description="What initiated this retrain (manual | scheduled)."
    )


class TrainingRunTriggerResponse(CamelModel):
    """The 202 acknowledgement that a retrain Job was submitted (+ the eligibility counts)."""

    job_id: str = Field(..., description="The submitted background-job id (training runs async).")
    trigger: str = Field(..., description="What initiated this retrain (manual | scheduled).")
    status: str = Field(..., description="Submission status (always 'submitted' here).")
    label_total: int = Field(
        ..., ge=0, description="Matured reviewed labels available to train on."
    )
    label_positives: int = Field(..., ge=0, description="Matured fraud-positive target labels.")
    label_negatives: int = Field(..., ge=0, description="Matured fraud-negative target labels.")


class TrainingRunView(CamelModel):
    """One model training run projected onto the API surface (PHI-free)."""

    training_run_id: str = Field(..., description="The training run's unique id (UUID).")
    trigger: str = Field(..., description="What initiated the run (manual | scheduled).")
    status: str = Field(..., description="Job status (pending|running|succeeded|failed).")
    dataset_id: str = Field(..., description="The training dataset manifest id (UUID).")
    artifact_uri: str | None = Field(default=None, description="Artifact bundle uri when trained.")
    metrics: dict[str, Any] = Field(
        default_factory=dict, description="PHI-free holdout metrics + gate verdict recorded."
    )
    created_at: datetime = Field(..., description="When the run was recorded.")


class TrainingRunListResponse(CamelModel):
    """The model training-run history, newest first."""

    training_runs: list[TrainingRunView] = Field(
        default_factory=list, description="Training runs, newest first."
    )


class CanaryRequest(CamelModel):
    """The canary ramp percent — only the documented 5/25/50/100 steps are accepted."""

    percent: Literal[5, 25, 50, 100] = Field(
        ..., description="Traffic percent for the canary; 100 promotes it to active."
    )


class DeploymentResponse(CamelModel):
    """The live active/canary deployment pointer projected onto the API surface (PHI-free)."""

    active_version_label: str = Field(..., description="The currently active model version label.")
    canary_version_label: str | None = Field(
        default=None, description="The canary candidate's label, or None when no rollout is live."
    )
    canary_percent: int = Field(..., ge=0, le=100, description="Percent of traffic on the canary.")
    previous_active_version_label: str | None = Field(
        default=None, description="The prior active label retained for rollback, if any."
    )
    updated_at: datetime = Field(..., description="When the pointer was last changed.")


class RollbackResponse(CamelModel):
    """What a rollback did (aborted a canary or restored the previous active) + the new pointer."""

    action: str = Field(..., description="'canary_aborted' or 'restored_previous'.")
    deployment: DeploymentResponse = Field(
        ..., description="The deployment pointer after rollback."
    )


class CanaryEvaluationResponse(CamelModel):
    """The canary auto-abort verdict + the per-arm inference stats behind it (plan §10.5.1)."""

    aborted: bool = Field(..., description="True when the canary deviated enough to auto-rollback.")
    active_count: int = Field(..., ge=0, description="Active-arm inference samples in the window.")
    active_mean: float = Field(..., description="Active-arm mean predicted probability.")
    canary_count: int = Field(..., ge=0, description="Canary-arm inference samples in the window.")
    canary_mean: float = Field(..., description="Canary-arm mean predicted probability.")
    deviation: float = Field(..., ge=0.0, description="Absolute gap between the arms' mean scores.")
    deployment: DeploymentResponse = Field(
        ..., description="The deployment pointer after the check."
    )


class DriftReportView(CamelModel):
    """One advisory drift report projected onto the API surface (PHI-free)."""

    drift_report_id: str = Field(..., description="The drift report's unique id (UUID).")
    version_label: str = Field(..., description="The model version the report concerns.")
    window: str = Field(..., description="The window the report was computed over.")
    severity: Severity = Field(..., description="Advisory severity band derived from the PSI.")
    advisory: bool = Field(..., description="Always true — drift never gates serving (plan §10.5).")
    metrics: dict[str, Any] = Field(
        default_factory=dict, description="PHI-free drift metrics (PSI, sample counts, means)."
    )
    created_at: datetime = Field(..., description="When the report was produced.")


class DriftReportListResponse(CamelModel):
    """Advisory drift reports, newest first."""

    drift_reports: list[DriftReportView] = Field(
        default_factory=list, description="Advisory drift reports, newest first."
    )

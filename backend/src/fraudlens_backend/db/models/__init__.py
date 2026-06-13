"""FraudLens ORM models (plan §9). Importing this package registers every table on
``Base.metadata`` — Alembic's env, the ERD generator, and the tenancy checker rely on that.
Re-exports are intentional (the model classes, the shared ``Base``, and the column enums).
"""

from __future__ import annotations

from fraudlens_backend.db.base import Base
from fraudlens_backend.db.models.alerts import Alert, AlertAction, SarDraft
from fraudlens_backend.db.models.analysis import (
    AnalysisResult,
    AnalysisRun,
    AnalysisRunEvent,
    RagRetrieval,
)
from fraudlens_backend.db.models.core import Agency, AmlRule, Transaction, User
from fraudlens_backend.db.models.enums import (
    AlertActionType,
    AlertStatus,
    AmlRuleType,
    AnalysisRunEventType,
    JobStatus,
    JobType,
    LabelSource,
    ModelTrigger,
    ModelVersionStatus,
    RunStatus,
    SarStatus,
    Severity,
    TrainingLabelType,
    UserRole,
)
from fraudlens_backend.db.models.mlops import (
    DriftReport,
    ModelDeployment,
    ModelEvaluation,
    ModelInferenceLog,
    ModelTrainingRun,
    ModelVersion,
    TrainingDataset,
    TrainingLabel,
)
from fraudlens_backend.db.models.ops import AuditLog, JobExecution, SystemConfig

# Platform (non-tenant) tables — carry no `agency_id`. Mirrored by scripts/check_tenancy.py.
PLATFORM_TABLES: frozenset[str] = frozenset(
    {
        "agencies",
        "training_datasets",
        "model_training_runs",
        "model_versions",
        "model_evaluations",
        "model_deployments",
        "drift_reports",
    }
)

__all__ = [
    "PLATFORM_TABLES",
    "Agency",
    "Alert",
    "AlertAction",
    "AlertActionType",
    "AlertStatus",
    "AmlRule",
    "AmlRuleType",
    "AnalysisResult",
    "AnalysisRun",
    "AnalysisRunEvent",
    "AnalysisRunEventType",
    "AuditLog",
    "Base",
    "DriftReport",
    "JobExecution",
    "JobStatus",
    "JobType",
    "LabelSource",
    "ModelDeployment",
    "ModelEvaluation",
    "ModelInferenceLog",
    "ModelTrainingRun",
    "ModelTrigger",
    "ModelVersion",
    "ModelVersionStatus",
    "RagRetrieval",
    "RunStatus",
    "SarDraft",
    "SarStatus",
    "Severity",
    "SystemConfig",
    "TrainingDataset",
    "TrainingLabel",
    "TrainingLabelType",
    "Transaction",
    "User",
    "UserRole",
]

"""Summary: String enumerations used as ORM column types across the FraudLens schema
(plan §9). Each is a `StrEnum` so it stores its lowercase/dotted string value in the
database (columns use `Enum(..., native_enum=False)`, so the value is what persists) and
serializes cleanly on the camelCase API surface. Centralizing them here keeps the domain
vocabulary in one place (no duplication, rule 5) and lets models, repositories, the seed,
and later phases share the exact same members. `RiskBand` and `AmlRuleType` are intentionally
NOT redefined here — they are canonical in `fraudlens_core` (the scoring band and the rules-
engine taxonomy) and are reused directly, since the pure rules engine dispatches on the type.

Key classes:
- UserRole: a user's RBAC role within an agency.
- Severity: ordinal severity shared by rules, alerts, and drift reports.
- RunStatus: lifecycle status of an analysis run.
- AlertStatus: lifecycle status of an alert.
- AlertActionType: a review action recorded against an alert.
- SarStatus: lifecycle status of a SAR draft.
- AnalysisRunEventType: the persisted ordered event types backing SSE replay (§9.1).
- JobType: the kind of background job recorded in `job_executions`.
- JobStatus: lifecycle status of a background job execution.
- ModelTrigger: what initiated a model training run.
- ModelVersionStatus: registry lifecycle state of a model version.
- TrainingLabelType: the outcome label produced by a reviewed decision.
- LabelSource: the provenance of a training label.

Key functions:
- (none)

Notes:
- Members are stored as their string values (`native_enum=False`); never reorder or rename
  a value without a migration, since persisted rows hold the literal string.
- `AnalysisRunEventType` values are dotted (e.g. `step.rules.completed`) to match the SSE
  event names in plan §5.4 / §9.1.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """A user's RBAC role within an agency (plan §6.3)."""

    ANALYST = "analyst"
    REVIEWER = "reviewer"
    ADMIN = "admin"


class Severity(StrEnum):
    """Ordinal severity shared by AML rules, alerts, and drift reports."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RunStatus(StrEnum):
    """Lifecycle status of an analysis run (plan §9.1 `analysis_runs`)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AlertStatus(StrEnum):
    """Lifecycle status of an alert (plan §9.1 `alerts`)."""

    OPEN = "open"
    PENDING_REVIEW = "pending_review"
    IN_REVIEW = "in_review"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class AlertActionType(StrEnum):
    """A review action recorded against an alert (plan §5.4)."""

    ASSIGN = "assign"
    COMMENT = "comment"
    ESCALATE = "escalate"
    RESOLVE = "resolve"
    DISMISS = "dismiss"


class SarStatus(StrEnum):
    """Lifecycle status of a SAR draft (plan §9.1 `sar_drafts`)."""

    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class AnalysisRunEventType(StrEnum):
    """Persisted ordered event types backing SSE replay (plan §5.4 / §9.1)."""

    RUN_STARTED = "run.started"
    STEP_RULES_COMPLETED = "step.rules.completed"
    STEP_SCORING_COMPLETED = "step.scoring.completed"
    STEP_SHAP_COMPLETED = "step.shap.completed"
    STEP_RAG_COMPLETED = "step.rag.completed"
    SAR_STARTED = "sar.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


class JobType(StrEnum):
    """Kind of background job recorded in `job_executions` (plan §9.1)."""

    SEED = "seed"
    CSV_IMPORT = "csv_import"
    TRAIN = "train"
    RETRAIN = "retrain"
    INGEST_RAG = "ingest_rag"
    DRIFT_SCAN = "drift_scan"
    BATCH_SCORE = "batch_score"


class JobStatus(StrEnum):
    """Lifecycle status of a background job execution."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ModelTrigger(StrEnum):
    """What initiated a model training run (plan §9.2)."""

    MANUAL = "manual"
    SCHEDULED = "scheduled"


class ModelVersionStatus(StrEnum):
    """Registry lifecycle state of a model version (plan §9.2 / §10.5)."""

    CANDIDATE = "candidate"
    SHADOW = "shadow"
    CANARY = "canary"
    ACTIVE = "active"
    ARCHIVED = "archived"
    REJECTED = "rejected"


class TrainingLabelType(StrEnum):
    """Outcome label produced by a reviewed decision (plan §9.2)."""

    CONFIRMED_FRAUD = "confirmed_fraud"
    FALSE_POSITIVE = "false_positive"
    FALSE_NEGATIVE = "false_negative"
    BENIGN = "benign"


class LabelSource(StrEnum):
    """Provenance of a training label (only matured reviewed decisions, plan §9.2)."""

    ANALYST_REVIEW = "analyst_review"

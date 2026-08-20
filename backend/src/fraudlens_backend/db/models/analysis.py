"""Summary: The analysis-pipeline tables (plan §9.1): `analysis_runs` (the persisted
investigation run), the immutable `analysis_results` (scoring + SHAP + rule-hit snapshot,
one per run), `rag_retrievals` (the regulatory citations retrieved for a run), and
`analysis_run_events` (the ordered, append-only event log that backs SSE replay from
`Last-Event-ID`, ADR-016). All four are tenant-scoped (NOT NULL `agency_id`, indexed) and
reference their run; `analysis_results` and `rag_retrievals` are one-to-one with a run
(UNIQUE `run_id`). Event payloads are masked / PHI-free (the authoritative SAR is persisted
separately in `sar_drafts`).

Key classes:
- AnalysisRun: a persisted investigation run with status + version provenance.
- AnalysisResult: the immutable scoring/SHAP/rule-hit snapshot for a run.
- RagRetrieval: the regulatory passages retrieved for a run (with citations).
- AnalysisRunEvent: one ordered event in a run's log (backs SSE replay).

Key functions:
- (none)

Notes:
- `model_version` / `rules_version` / `rag_version` / `prompt_version` are nullable on a
  run because they are filled in as each pipeline step completes.
- `idempotency_key` stores only a SHA-256 digest and is unique within an agency, so
  duplicate submissions survive process restarts without retaining a client-supplied key.
- `analysis_run_events` has UNIQUE `(run_id, seq)` for gap-free ordering and indexes
  `(agency_id, run_id, seq)` so replay reads stay tenant-scoped (plan §9.1).
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from fraudlens_backend.db.base import (
    JSONB_TYPE,
    AgencyScopedMixin,
    Base,
    CreatedAtMixin,
    JsonValue,
    TimestampMixin,
    str_enum,
)
from fraudlens_backend.db.models.enums import AnalysisRunEventType, RunStatus
from fraudlens_core import RiskBand


class AnalysisRun(AgencyScopedMixin, TimestampMixin, Base):
    """A persisted investigation run with status and per-step version provenance."""

    __tablename__ = "analysis_runs"
    __table_args__ = (
        Index("ix_analysis_runs_agency_id_status", "agency_id", "status"),
        Index("ix_analysis_runs_agency_id_created_at", "agency_id", "created_at"),
        UniqueConstraint(
            "agency_id",
            "idempotency_key",
            name="uq_analysis_runs_agency_id_idempotency_key",
        ),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("transactions.id"), nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus), nullable=False, default=RunStatus.PENDING
    )
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_band: Mapped[RiskBand | None] = mapped_column(str_enum(RiskBand), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rules_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rag_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    workflow_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="single_writer", server_default="single_writer"
    )
    graph_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AnalysisResult(AgencyScopedMixin, CreatedAtMixin, Base):
    """The immutable scoring/SHAP/rule-hit snapshot for a run (one per run)."""

    __tablename__ = "analysis_results"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_analysis_results_run_id"),
        Index("ix_analysis_results_agency_id", "agency_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("analysis_runs.id"), nullable=False)
    fraud_probability: Mapped[float] = mapped_column(Float, nullable=False)
    shap_values: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    top_features: Mapped[list[JsonValue]] = mapped_column(JSONB_TYPE, nullable=False, default=list)
    rule_hits: Mapped[list[JsonValue]] = mapped_column(JSONB_TYPE, nullable=False, default=list)
    combined_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_band: Mapped[RiskBand] = mapped_column(str_enum(RiskBand), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)


class RagRetrieval(AgencyScopedMixin, CreatedAtMixin, Base):
    """The regulatory passages retrieved for a run, with citations (one per run)."""

    __tablename__ = "rag_retrievals"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_rag_retrievals_run_id"),
        Index("ix_rag_retrievals_agency_id", "agency_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("analysis_runs.id"), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    chunks: Mapped[list[JsonValue]] = mapped_column(JSONB_TYPE, nullable=False, default=list)
    rag_version: Mapped[str] = mapped_column(String(128), nullable=False)


class AnalysisRunEvent(AgencyScopedMixin, CreatedAtMixin, Base):
    """One ordered event in a run's append-only log (backs SSE replay, ADR-016)."""

    __tablename__ = "analysis_run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_analysis_run_events_run_id"),
        Index("ix_analysis_run_events_agency_id_run_id_seq", "agency_id", "run_id", "seq"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("analysis_runs.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[AnalysisRunEventType] = mapped_column(
        str_enum(AnalysisRunEventType), nullable=False
    )
    payload: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False, default=dict)

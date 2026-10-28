"""Summary: Trusted person links, immutable case submissions, participation and command receipts.

Key classes:
- Person: canonical tenant-scoped human identity provisioned outside application roles.
- AccountPerson: immutable trusted account-to-person binding.
- CaseContributor: accumulated human preparation participation for a case.
- CaseSubmission: immutable preparation snapshot with independently mutable PDF readiness.
- CaseDecision: append-only checker decision referencing its masked action reason.
- WorkflowReceipt: durable idempotent command response.

Key functions:
- (none)

Notes:
- Composite foreign keys prevent cross-tenant identity and submission references.
- Historical accounts remain unmapped until trusted provisioning supplies evidence.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from fraudlens_backend.db.base import (
    JSONB_TYPE,
    AgencyScopedMixin,
    Base,
    CreatedAtMixin,
    JsonValue,
)


class Person(AgencyScopedMixin, CreatedAtMixin, Base):
    """Canonical human identity; no application-admin mutation surface."""

    __tablename__ = "persons"
    __table_args__ = (
        UniqueConstraint("agency_id", "id", name="uq_persons_agency_id_id"),
        Index("ix_persons_agency_id", "agency_id"),
    )


class AccountPerson(AgencyScopedMixin, CreatedAtMixin, Base):
    """Trusted account binding that may never be remapped or removed."""

    __tablename__ = "account_persons"
    __table_args__ = (
        UniqueConstraint("agency_id", "account_id", name="uq_account_persons_agency_account"),
        ForeignKeyConstraint(["agency_id", "account_id"], ["users.agency_id", "users.id"]),
        ForeignKeyConstraint(["agency_id", "person_id"], ["persons.agency_id", "persons.id"]),
        Index("ix_account_persons_agency_id_person_id", "agency_id", "person_id"),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    person_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)


class CaseContributor(AgencyScopedMixin, CreatedAtMixin, Base):
    """Append-only participation; comments and returns do not add contributors."""

    __tablename__ = "case_contributors"
    __table_args__ = (
        UniqueConstraint("agency_id", "alert_id", "person_id", name="uq_case_contributors_person"),
        ForeignKeyConstraint(["agency_id", "alert_id"], ["alerts.agency_id", "alerts.id"]),
        ForeignKeyConstraint(["agency_id", "person_id"], ["persons.agency_id", "persons.id"]),
        Index("ix_case_contributors_agency_id_alert_id", "agency_id", "alert_id"),
    )
    alert_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    person_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)


class CaseSubmission(AgencyScopedMixin, CreatedAtMixin, Base):
    """One frozen submission round. Reasons live only in the referenced action."""

    __tablename__ = "case_submissions"
    __table_args__ = (
        UniqueConstraint("agency_id", "id", name="uq_case_submissions_agency_id_id"),
        UniqueConstraint("agency_id", "alert_id", "round", name="uq_case_submissions_round"),
        ForeignKeyConstraint(["agency_id", "alert_id"], ["alerts.agency_id", "alerts.id"]),
        ForeignKeyConstraint(["agency_id", "maker_person_id"], ["persons.agency_id", "persons.id"]),
        ForeignKeyConstraint(
            ["agency_id", "sar_draft_id"], ["sar_drafts.agency_id", "sar_drafts.id"]
        ),
        Index("ix_case_submissions_agency_id_alert_id", "agency_id", "alert_id"),
    )
    alert_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    maker_account_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    maker_person_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    sar_draft_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    sar_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recommendation: Mapped[str] = mapped_column(String(16), nullable=False)
    contributor_person_ids: Mapped[list[str]] = mapped_column(JSONB_TYPE, nullable=False)
    evidence: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False)
    action_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    pdf_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unavailable", server_default="unavailable"
    )


class CaseDecision(AgencyScopedMixin, CreatedAtMixin, Base):
    """One terminal or return decision per round, persisted with its action and label."""

    __tablename__ = "case_decisions"
    __table_args__ = (
        UniqueConstraint("agency_id", "submission_id", name="uq_case_decisions_submission"),
        ForeignKeyConstraint(
            ["agency_id", "submission_id"], ["case_submissions.agency_id", "case_submissions.id"]
        ),
        ForeignKeyConstraint(["agency_id", "actor_person_id"], ["persons.agency_id", "persons.id"]),
        Index("ix_case_decisions_agency_id_submission_id", "agency_id", "submission_id"),
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    actor_account_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    actor_person_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)


class WorkflowReceipt(AgencyScopedMixin, CreatedAtMixin, Base):
    """Serialized command result makes identical retries independent of later case state."""

    __tablename__ = "workflow_receipts"
    __table_args__ = (
        UniqueConstraint("agency_id", "alert_id", "key_digest", name="uq_workflow_receipts_key"),
        Index("ix_workflow_receipts_agency_id_alert_id", "agency_id", "alert_id"),
    )
    alert_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    key_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[JsonValue] = mapped_column(JSONB_TYPE, nullable=False)

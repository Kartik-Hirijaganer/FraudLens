"""trusted person identity and immutable maker checker submissions

Revision ID: 0016_maker_checker
Revises: 0015_account_identity_keys
Create Date: 2026-10-25 08:21:27.912648
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0016_maker_checker"
down_revision: str | None = "0015_account_identity_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("users", "alerts", "sar_drafts"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.create_unique_constraint(f"uq_{table}_agency_id_id", ["agency_id", "id"])
    op.create_table(
        "persons",
        sa.Column("agency_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agency_id"], ["agencies.id"], name=op.f("fk_persons_agency_id_agencies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_persons")),
        sa.UniqueConstraint("agency_id", "id", name="uq_persons_agency_id_id"),
    )
    with op.batch_alter_table("persons", schema=None) as batch_op:
        batch_op.create_index("ix_persons_agency_id", ["agency_id"], unique=False)

    op.create_table(
        "workflow_receipts",
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("key_digest", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "response",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("agency_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agency_id"], ["agencies.id"], name=op.f("fk_workflow_receipts_agency_id_agencies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_receipts")),
        sa.UniqueConstraint("agency_id", "alert_id", "key_digest", name="uq_workflow_receipts_key"),
    )
    with op.batch_alter_table("workflow_receipts", schema=None) as batch_op:
        batch_op.create_index(
            "ix_workflow_receipts_agency_id_alert_id", ["agency_id", "alert_id"], unique=False
        )

    op.create_table(
        "account_persons",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("agency_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agency_id", "account_id"],
            ["users.agency_id", "users.id"],
            name=op.f("fk_account_persons_agency_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["agency_id", "person_id"],
            ["persons.agency_id", "persons.id"],
            name=op.f("fk_account_persons_agency_id_persons"),
        ),
        sa.ForeignKeyConstraint(
            ["agency_id"], ["agencies.id"], name=op.f("fk_account_persons_agency_id_agencies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_persons")),
        sa.UniqueConstraint("agency_id", "account_id", name="uq_account_persons_agency_account"),
    )
    with op.batch_alter_table("account_persons", schema=None) as batch_op:
        batch_op.create_index(
            "ix_account_persons_agency_id_person_id", ["agency_id", "person_id"], unique=False
        )

    op.create_table(
        "case_contributors",
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("agency_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agency_id", "alert_id"],
            ["alerts.agency_id", "alerts.id"],
            name=op.f("fk_case_contributors_agency_id_alerts"),
        ),
        sa.ForeignKeyConstraint(
            ["agency_id", "person_id"],
            ["persons.agency_id", "persons.id"],
            name=op.f("fk_case_contributors_agency_id_persons"),
        ),
        sa.ForeignKeyConstraint(
            ["agency_id"], ["agencies.id"], name=op.f("fk_case_contributors_agency_id_agencies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_contributors")),
        sa.UniqueConstraint(
            "agency_id", "alert_id", "person_id", name="uq_case_contributors_person"
        ),
    )
    with op.batch_alter_table("case_contributors", schema=None) as batch_op:
        batch_op.create_index(
            "ix_case_contributors_agency_id_alert_id", ["agency_id", "alert_id"], unique=False
        )

    op.create_table(
        "case_submissions",
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("maker_account_id", sa.Uuid(), nullable=False),
        sa.Column("maker_person_id", sa.Uuid(), nullable=False),
        sa.Column("sar_draft_id", sa.Uuid(), nullable=True),
        sa.Column("sar_version", sa.Integer(), nullable=True),
        sa.Column("recommendation", sa.String(length=16), nullable=False),
        sa.Column(
            "contributor_person_ids",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "evidence",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("action_id", sa.Uuid(), nullable=False),
        sa.Column("pdf_status", sa.String(length=16), server_default="unavailable", nullable=False),
        sa.Column("agency_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agency_id", "alert_id"],
            ["alerts.agency_id", "alerts.id"],
            name=op.f("fk_case_submissions_agency_id_alerts"),
        ),
        sa.ForeignKeyConstraint(
            ["agency_id", "maker_person_id"],
            ["persons.agency_id", "persons.id"],
            name=op.f("fk_case_submissions_agency_id_persons"),
        ),
        sa.ForeignKeyConstraint(
            ["agency_id", "sar_draft_id"],
            ["sar_drafts.agency_id", "sar_drafts.id"],
            name=op.f("fk_case_submissions_agency_id_sar_drafts"),
        ),
        sa.ForeignKeyConstraint(
            ["agency_id"], ["agencies.id"], name=op.f("fk_case_submissions_agency_id_agencies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_submissions")),
        sa.UniqueConstraint("agency_id", "alert_id", "round", name="uq_case_submissions_round"),
        sa.UniqueConstraint("agency_id", "id", name="uq_case_submissions_agency_id_id"),
    )
    with op.batch_alter_table("case_submissions", schema=None) as batch_op:
        batch_op.create_index(
            "ix_case_submissions_agency_id_alert_id", ["agency_id", "alert_id"], unique=False
        )

    op.create_table(
        "case_decisions",
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("actor_account_id", sa.Uuid(), nullable=False),
        sa.Column("actor_person_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("action_id", sa.Uuid(), nullable=False),
        sa.Column("agency_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agency_id", "actor_person_id"],
            ["persons.agency_id", "persons.id"],
            name=op.f("fk_case_decisions_agency_id_persons"),
        ),
        sa.ForeignKeyConstraint(
            ["agency_id", "submission_id"],
            ["case_submissions.agency_id", "case_submissions.id"],
            name=op.f("fk_case_decisions_agency_id_case_submissions"),
        ),
        sa.ForeignKeyConstraint(
            ["agency_id"], ["agencies.id"], name=op.f("fk_case_decisions_agency_id_agencies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_decisions")),
        sa.UniqueConstraint("agency_id", "submission_id", name="uq_case_decisions_submission"),
    )
    with op.batch_alter_table("case_decisions", schema=None) as batch_op:
        batch_op.create_index(
            "ix_case_decisions_agency_id_submission_id",
            ["agency_id", "submission_id"],
            unique=False,
        )

    with op.batch_alter_table("alert_actions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("actor_person_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("submission_id", sa.Uuid(), nullable=True))
        batch_op.alter_column(
            "action",
            existing_type=sa.VARCHAR(length=8),
            type_=sa.Enum(
                "assign",
                "comment",
                "escalate",
                "resolve",
                "dismiss",
                "submit",
                "approve",
                "return",
                "decline",
                "edit",
                "regenerate",
                name="alertactiontype",
                native_enum=False,
            ),
            existing_nullable=False,
        )

    with op.batch_alter_table("alerts") as batch_op:
        batch_op.add_column(
            sa.Column("case_version", sa.Integer(), server_default="1", nullable=False)
        )
        batch_op.add_column(sa.Column("owner_person_id", sa.Uuid(), nullable=True))
    with op.batch_alter_table("sar_drafts") as batch_op:
        batch_op.alter_column(
            "status", existing_type=sa.String(8), type_=sa.String(9), existing_nullable=False
        )
    with op.batch_alter_table("training_labels") as batch_op:
        batch_op.alter_column(
            "source", existing_type=sa.String(14), type_=sa.String(15), existing_nullable=False
        )
    _identity_guards()
    # ### end Alembic commands ###


def _identity_guards() -> None:
    """Person links are immutable even if application roles or accounts change."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION forbid_person_remap() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'trusted person bindings are immutable'; END $$""")
        op.execute(
            "CREATE TRIGGER account_person_immutable BEFORE UPDATE OR DELETE ON account_persons "
            "FOR EACH ROW EXECUTE FUNCTION forbid_person_remap()"
        )
        op.execute("""CREATE FUNCTION freeze_case_submission() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF (to_jsonb(NEW) - 'pdf_status') IS DISTINCT FROM
                    (to_jsonb(OLD) - 'pdf_status') THEN
                    RAISE EXCEPTION 'submitted preparation is immutable';
                END IF;
                RETURN NEW;
            END $$""")
        op.execute(
            "CREATE TRIGGER case_submission_immutable BEFORE UPDATE ON case_submissions "
            "FOR EACH ROW EXECUTE FUNCTION freeze_case_submission()"
        )
    else:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER account_person_no_{operation.lower()} BEFORE {operation} "
                "ON account_persons BEGIN SELECT RAISE(ABORT, "
                "'trusted person bindings are immutable'); END"
            )


def downgrade() -> None:
    """Remove additive records in dependency order; historical artifacts remain readable."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER account_person_immutable ON account_persons")
        op.execute("DROP FUNCTION forbid_person_remap()")
        op.execute("DROP TRIGGER case_submission_immutable ON case_submissions")
        op.execute("DROP FUNCTION freeze_case_submission()")
    for table in (
        "case_decisions",
        "case_submissions",
        "case_contributors",
        "account_persons",
        "workflow_receipts",
        "persons",
    ):
        op.drop_table(table)
    with op.batch_alter_table("alert_actions") as batch_op:
        batch_op.drop_column("submission_id")
        batch_op.drop_column("actor_person_id")
        batch_op.alter_column(
            "action", existing_type=sa.String(10), type_=sa.String(8), existing_nullable=False
        )
    with op.batch_alter_table("alerts") as batch_op:
        batch_op.drop_column("owner_person_id")
        batch_op.drop_column("case_version")
    with op.batch_alter_table("training_labels") as batch_op:
        batch_op.alter_column(
            "source", existing_type=sa.String(15), type_=sa.String(14), existing_nullable=False
        )
    # New lifecycle strings must be retired before a destructive downgrade, never silently mapped.
    with op.batch_alter_table("sar_drafts") as batch_op:
        batch_op.alter_column(
            "status", existing_type=sa.String(9), type_=sa.String(8), existing_nullable=False
        )
    for table in ("sar_drafts", "alerts", "users"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(f"uq_{table}_agency_id_id", type_="unique")

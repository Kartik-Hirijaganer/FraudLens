"""Persist the deterministic SAR quality verdict and its per-stage cascade attempts.

Adds the `sar_drafts.quality` verdict payload, retires the two-value `quality_status`
vocabulary in favour of the honest tri-state (`not_run` | `passed` | `failed`), and creates
the tenant-scoped ``sar_generation_attempts`` table that records one PHI-free row per cascade
stage. Historical rows are backfilled to ``not_run``: every pre-0.5.0 machine draft was written
as ``evaluated`` although no evaluator existed, so claiming those narratives passed a gate would
be false. The Postgres DDL hardening trigger installed by revision 0004 applies RLS/revokes to
the new table automatically.

Revision ID: 0012_sar_quality_gate
Revises: 0011_run_spend_reserve
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0012_sar_quality_gate"
down_revision: str | None = "0011_run_spend_reserve"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_sar_drafts_quality_status"
_PREVIOUS_VALUES = ("evaluated", "unevaluated")
_CURRENT_VALUES = ("not_run", "passed", "failed")
_PREVIOUS_DEFAULT = "evaluated"
_CURRENT_DEFAULT = "not_run"


def _json() -> sa.types.TypeEngine[object]:
    """Return JSONB on Postgres and generic JSON on SQLite."""
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _check(values: tuple[str, ...]) -> str:
    """Render the quality-status CHECK expression for one vocabulary."""
    return f"quality_status IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    """Record gate verdicts on drafts and per-stage attempts, backfilling honest history."""
    with op.batch_alter_table("sar_drafts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("quality", _json(), nullable=True))
        batch_op.drop_constraint(_CONSTRAINT, type_="check")
    op.execute(sa.text("UPDATE sar_drafts SET quality = '{}' WHERE quality IS NULL"))
    op.execute(sa.text(f"UPDATE sar_drafts SET quality_status = '{_CURRENT_DEFAULT}'"))
    with op.batch_alter_table("sar_drafts", schema=None) as batch_op:
        batch_op.alter_column("quality", existing_type=_json(), nullable=False)
        batch_op.alter_column(
            "quality_status",
            existing_type=sa.String(length=32),
            server_default=_CURRENT_DEFAULT,
            existing_nullable=False,
        )
        batch_op.create_check_constraint(_CONSTRAINT, _check(_CURRENT_VALUES))

    op.create_table(
        "sar_generation_attempts",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("connection", sa.String(length=64), nullable=True),
        sa.Column("served_model", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("reason_codes", _json(), nullable=False),
        sa.Column("quality", _json(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("token_usage", _json(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("prompt_hash", sa.String(length=128), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("agency_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agency_id"],
            ["agencies.id"],
            name=op.f("fk_sar_generation_attempts_agency_id_agencies"),
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["sar_drafts.id"],
            name=op.f("fk_sar_generation_attempts_draft_id_sar_drafts"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs.id"],
            name=op.f("fk_sar_generation_attempts_run_id_analysis_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sar_generation_attempts")),
        sa.UniqueConstraint(
            "draft_id", "ordinal", name="uq_sar_generation_attempts_draft_id_ordinal"
        ),
    )
    with op.batch_alter_table("sar_generation_attempts", schema=None) as batch_op:
        batch_op.create_index(
            "ix_sar_generation_attempts_agency_id_run_id",
            ["agency_id", "run_id"],
            unique=False,
        )


def downgrade() -> None:
    """Drop the attempt trail and restore the pre-0.5.0 quality vocabulary."""
    with op.batch_alter_table("sar_generation_attempts", schema=None) as batch_op:
        batch_op.drop_index("ix_sar_generation_attempts_agency_id_run_id")
    op.drop_table("sar_generation_attempts")

    with op.batch_alter_table("sar_drafts", schema=None) as batch_op:
        batch_op.drop_constraint(_CONSTRAINT, type_="check")
    op.execute(sa.text(f"UPDATE sar_drafts SET quality_status = '{_PREVIOUS_DEFAULT}'"))
    with op.batch_alter_table("sar_drafts", schema=None) as batch_op:
        batch_op.alter_column(
            "quality_status",
            existing_type=sa.String(length=32),
            server_default=_PREVIOUS_DEFAULT,
            existing_nullable=False,
        )
        batch_op.create_check_constraint(_CONSTRAINT, _check(_PREVIOUS_VALUES))
        batch_op.drop_column("quality")

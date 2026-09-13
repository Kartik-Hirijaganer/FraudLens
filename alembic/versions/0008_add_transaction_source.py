"""Add immutable transaction provenance and SAR narrative quality state.

Transaction source is the trusted input to the synthetic-only model-egress policy. Existing IBM
AML rows are identified from their committed feature marker; all other legacy rows fail closed as
unknown. SAR quality state is independent of review status so an edited narrative can be marked
unevaluated without changing its human-review lifecycle.

Revision ID: 0008_transaction_source
Revises: 0007_transaction_text_lengths
Create Date: 2026-09-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_transaction_source"
down_revision: str | None = "0007_transaction_text_lengths"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE_VALUES = (
    "unknown",
    "api-upload",
    "portfolio-demo",
    "ibm-aml-synthetic",
    "ieee-cis-sample",
    "synthetic-generator",
)
_QUALITY_VALUES = ("evaluated", "unevaluated")


def upgrade() -> None:
    """Add fail-closed defaults, backfill known IBM rows, and constrain both vocabularies."""
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("source", sa.String(length=32), server_default="unknown", nullable=False)
        )
        batch_op.create_check_constraint(
            "ck_transactions_source",
            f"source IN ({', '.join(repr(value) for value in _SOURCE_VALUES)})",
        )
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "UPDATE transactions SET source = 'ibm-aml-synthetic' "
                "WHERE features ->> 'dataset_source' = 'ibm-aml'"
            )
        )
    else:
        connection.execute(
            sa.text(
                "UPDATE transactions SET source = 'ibm-aml-synthetic' "
                "WHERE json_extract(features, '$.dataset_source') = 'ibm-aml'"
            )
        )
    with op.batch_alter_table("sar_drafts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "quality_status", sa.String(length=32), server_default="evaluated", nullable=False
            )
        )
        batch_op.create_check_constraint(
            "ck_sar_drafts_quality_status",
            f"quality_status IN ({', '.join(repr(value) for value in _QUALITY_VALUES)})",
        )


def downgrade() -> None:
    """Remove SAR quality state and transaction provenance."""
    with op.batch_alter_table("sar_drafts", schema=None) as batch_op:
        batch_op.drop_constraint("ck_sar_drafts_quality_status", type_="check")
        batch_op.drop_column("quality_status")
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_constraint("ck_transactions_source", type_="check")
        batch_op.drop_column("source")

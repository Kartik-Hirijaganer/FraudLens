"""Link durable screening runs to their accepted CSV import.

Revision ID: 0014_upload_screening
Revises: 0013_sar_attempt_created_at
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_upload_screening"
down_revision: str | None = "0013_sar_attempt_created_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable job association without backfilling existing transactions."""
    op.add_column("sar_drafts", sa.Column("error_code", sa.String(64), nullable=True))
    with op.batch_alter_table("analysis_runs") as batch:
        batch.add_column(sa.Column("import_job_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_analysis_runs_import_job_id_job_executions",
            "job_executions",
            ["import_job_id"],
            ["id"],
        )
        batch.create_index(
            "ix_analysis_runs_agency_id_import_job_id", ["agency_id", "import_job_id"]
        )


def downgrade() -> None:
    """Remove the additive upload association."""
    op.drop_column("sar_drafts", "error_code")
    with op.batch_alter_table("analysis_runs") as batch:
        batch.drop_index("ix_analysis_runs_agency_id_import_job_id")
        batch.drop_constraint("fk_analysis_runs_import_job_id_job_executions", type_="foreignkey")
        batch.drop_column("import_job_id")

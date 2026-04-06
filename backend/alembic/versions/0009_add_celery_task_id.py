"""add celery_task_id to pipeline_runs

Revision ID: 0009
Revises: 0008
"""

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column("celery_task_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pipeline_runs", "celery_task_id")

"""add_pipeline_run_status_index

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-05

"""
from typing import Sequence, Union

from alembic import op


revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_pipeline_runs_status_created",
        "pipeline_runs",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_status_created", table_name="pipeline_runs")

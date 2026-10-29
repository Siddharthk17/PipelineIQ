"""add_pipeline_runs_user_created_index

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-29

The ORM declares ix_pipeline_runs_user_created(user_id, created_at) but no
migration ever created it (0012 only added the status index). The per-user
run listing filters on (user_id, created_at) and was scanning.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_pipeline_runs_user_created",
        "pipeline_runs",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_user_created", table_name="pipeline_runs")

"""Add index on asset_relationships(target_id, pipeline_name, run_id) for pipeline aggregation.

Revision: g0a2b3c4d5e6
Revises: g0a1b2c3d4e5
Create Date: 2026-05-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "g0a2b3c4d5e6"
down_revision: Union[str, None] = "g0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_asset_rel_target_pipeline_run "
            "ON asset_relationships (target_id, pipeline_name, run_id)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_asset_rel_target_pipeline_run")

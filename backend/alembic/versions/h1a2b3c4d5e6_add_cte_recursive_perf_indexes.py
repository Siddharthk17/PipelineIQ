"""Add covering index for recursive CTE blast radius and upstream lineage.

The recursive JOIN in WITH RECURSIVE traverses asset_relationships
by source_id (downstream) and target_id (upstream). Without proper
covering indexes, each recursive step does a sequential scan, creating
the Cartesian product that fills disk.

Revision: h1a2b3c4d5e6
Revises: g0a2b3c4d5e6
Create Date: 2026-05-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "h1a2b3c4d5e6"
down_revision: Union[str, None] = "g0a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        op.execute(
            "SET LOCAL statement_timeout = '30s'"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_asset_rel_source_target "
            "ON asset_relationships (source_id, target_id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_asset_rel_target_source "
            "ON asset_relationships (target_id, source_id)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_asset_rel_target_source")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_asset_rel_source_target")
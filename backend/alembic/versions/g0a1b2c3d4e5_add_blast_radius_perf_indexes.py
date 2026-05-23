"""Add B-tree index on data_assets.name and partial index for blast radius CTE seed.

Revision: g0a1b2c3d4e5
Revises: 0016_catalog_indexes
Create Date: 2026-05-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "g0a1b2c3d4e5"
down_revision: Union[str, None] = "0016_catalog_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_data_assets_name_btree "
            "ON data_assets (name)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_asset_rel_source_id_pipeline "
            "ON asset_relationships (source_id, pipeline_name, run_id)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_asset_rel_source_id_pipeline")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_data_assets_name_btree")
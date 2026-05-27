"""Add composite indexes for catalog blast radius queries.

Revision ID: 0016_catalog_indexes
Revises: 0015
Create Date: 2026-05-23
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0016_catalog_indexes"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_asset_rel_source_target
            ON asset_relationships(source_id, target_id)
        """)
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_asset_rel_target_source
            ON asset_relationships(target_id, source_id)
        """)
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_data_assets_type_name
            ON data_assets(asset_type, name)
        """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_asset_rel_source_target")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_asset_rel_target_source")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_data_assets_type_name")
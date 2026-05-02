"""fix missing performance indexes

Revision ID: b315b6809272
Revises: 29058ddf0eae
Create Date: 2026-05-02 00:58:26.552261

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b315b6809272'
down_revision: Union[str, None] = '29058ddf0eae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pipeline_runs_user_created
            ON pipeline_runs(user_id, created_at DESC)
        """)
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_step_results_run_id
            ON step_results(pipeline_run_id, step_index)
        """)
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pipeline_runs_pipeline_name
            ON pipeline_runs(name, created_at DESC)
        """)
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_lineage_graphs_run_id
            ON lineage_graphs(pipeline_run_id)
        """)

def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_pipeline_runs_user_created")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_step_results_run_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_pipeline_runs_pipeline_name")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_lineage_graphs_run_id")

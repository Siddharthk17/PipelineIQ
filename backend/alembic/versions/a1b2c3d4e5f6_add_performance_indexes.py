"""add_performance_indexes

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-03-12
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def _table_names(bind) -> set[str]:
    inspector = sa.inspect(bind)
    return set(inspector.get_table_names())


def _execute_if_table_exists(bind, table_names: set[str], table_name: str, sql: str) -> None:
    if table_name not in table_names:
        return
    with op.get_context().autocommit_block():
        op.execute(sql)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    table_names = _table_names(bind)

    with op.get_context().autocommit_block():
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    _execute_if_table_exists(
        bind,
        table_names,
        "pipeline_runs",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pipeline_runs_user_created
        ON pipeline_runs(user_id, created_at DESC)
        """,
    )
    _execute_if_table_exists(
        bind,
        table_names,
        "step_results",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_step_results_run_id
        ON step_results(pipeline_run_id, step_index)
        """,
    )
    _execute_if_table_exists(
        bind,
        table_names,
        "schedule_runs",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_schedule_runs_schedule_id
        ON schedule_runs(schedule_id, triggered_at DESC)
        """,
    )
    _execute_if_table_exists(
        bind,
        table_names,
        "data_assets",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_data_assets_name_trgm
        ON data_assets USING gin(name gin_trgm_ops)
        """,
    )
    _execute_if_table_exists(
        bind,
        table_names,
        "pipeline_runs",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pipeline_runs_pipeline_name
        ON pipeline_runs(name, created_at DESC)
        """,
    )
    _execute_if_table_exists(
        bind,
        table_names,
        "pipeline_schedules",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pipeline_schedules_active
        ON pipeline_schedules(is_active, next_run_at)
        WHERE is_active = true
        """,
    )
    _execute_if_table_exists(
        bind,
        table_names,
        "lineage_graphs",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_lineage_graphs_run_id
        ON lineage_graphs(pipeline_run_id)
        """,
    )
    _execute_if_table_exists(
        bind,
        table_names,
        "asset_relationships",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_asset_relationships_source
        ON asset_relationships(source_id)
        """,
    )
    _execute_if_table_exists(
        bind,
        table_names,
        "asset_relationships",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_asset_relationships_target
        ON asset_relationships(target_id)
        """,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    drop_statements = (
        "DROP INDEX CONCURRENTLY IF EXISTS idx_pipeline_runs_user_created",
        "DROP INDEX CONCURRENTLY IF EXISTS idx_step_results_run_id",
        "DROP INDEX CONCURRENTLY IF EXISTS idx_schedule_runs_schedule_id",
        "DROP INDEX CONCURRENTLY IF EXISTS idx_data_assets_name_trgm",
        "DROP INDEX CONCURRENTLY IF EXISTS idx_pipeline_runs_pipeline_name",
        "DROP INDEX CONCURRENTLY IF EXISTS idx_pipeline_schedules_active",
        "DROP INDEX CONCURRENTLY IF EXISTS idx_lineage_graphs_run_id",
        "DROP INDEX CONCURRENTLY IF EXISTS idx_asset_relationships_source",
        "DROP INDEX CONCURRENTLY IF EXISTS idx_asset_relationships_target",
    )
    for statement in drop_statements:
        with op.get_context().autocommit_block():
            op.execute(statement)

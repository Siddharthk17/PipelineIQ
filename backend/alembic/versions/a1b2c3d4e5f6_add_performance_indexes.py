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


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(ix.get("name") == index_name for ix in inspector.get_indexes(table_name))


def _create_index_if_possible(bind, index_name: str, table_name: str, columns: list[str]) -> None:
    if not _table_exists(bind, table_name):
        return
    if _index_exists(bind, table_name, index_name):
        return
    op.create_index(index_name, table_name, columns, unique=False)


def _drop_index_if_exists(bind, index_name: str, table_name: str) -> None:
    if not _table_exists(bind, table_name):
        return
    if not _index_exists(bind, table_name, index_name):
        return
    op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    bind = op.get_bind()

    # 1) pipeline run list by user
    _create_index_if_possible(
        bind,
        "ix_pipeline_runs_user_id_created_at",
        "pipeline_runs",
        ["user_id", "created_at"],
    )

    # 2) pipeline run status filtering
    _create_index_if_possible(bind, "ix_pipeline_runs_status", "pipeline_runs", ["status"])

    # 3) pipeline run created_at ordering
    _create_index_if_possible(bind, "ix_pipeline_runs_created_at", "pipeline_runs", ["created_at"])

    # 4) step results lookup by run
    _create_index_if_possible(
        bind,
        "ix_step_results_pipeline_run_id",
        "step_results",
        ["pipeline_run_id"],
    )

    # 5) step results ordering inside a run
    _create_index_if_possible(
        bind,
        "ix_step_results_pipeline_run_id_step_index",
        "step_results",
        ["pipeline_run_id", "step_index"],
    )

    # 6) webhook delivery filtering by webhook
    _create_index_if_possible(
        bind,
        "ix_webhook_deliveries_webhook_id",
        "webhook_deliveries",
        ["webhook_id"],
    )

    # 7) webhook delivery run lookups
    _create_index_if_possible(
        bind,
        "ix_webhook_deliveries_run_id",
        "webhook_deliveries",
        ["run_id"],
    )

    # 8) schedule polling for active next runs
    _create_index_if_possible(
        bind,
        "ix_pipeline_schedules_is_active_next_run_at",
        "pipeline_schedules",
        ["is_active", "next_run_at"],
    )

    # 9) audit listing by user recency
    _create_index_if_possible(
        bind,
        "ix_audit_logs_user_id_created_at",
        "audit_logs",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    _drop_index_if_exists(bind, "ix_audit_logs_user_id_created_at", "audit_logs")
    _drop_index_if_exists(
        bind,
        "ix_pipeline_schedules_is_active_next_run_at",
        "pipeline_schedules",
    )
    _drop_index_if_exists(bind, "ix_webhook_deliveries_run_id", "webhook_deliveries")
    _drop_index_if_exists(bind, "ix_webhook_deliveries_webhook_id", "webhook_deliveries")
    _drop_index_if_exists(
        bind,
        "ix_step_results_pipeline_run_id_step_index",
        "step_results",
    )
    _drop_index_if_exists(bind, "ix_step_results_pipeline_run_id", "step_results")
    _drop_index_if_exists(bind, "ix_pipeline_runs_created_at", "pipeline_runs")
    _drop_index_if_exists(bind, "ix_pipeline_runs_status", "pipeline_runs")
    _drop_index_if_exists(
        bind,
        "ix_pipeline_runs_user_id_created_at",
        "pipeline_runs",
    )

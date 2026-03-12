"""add_performance_indexes

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-03-12
"""

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pipeline_runs.status — filtered on every list/stats query
    op.create_index("ix_pipeline_runs_status", "pipeline_runs", ["status"])
    # pipeline_runs.created_at — sorted on every list query
    op.create_index("ix_pipeline_runs_created_at", "pipeline_runs", ["created_at"])
    # step_results.pipeline_run_id — joined frequently
    op.create_index("ix_step_results_pipeline_run_id", "step_results", ["pipeline_run_id"])
    # webhook_deliveries.webhook_id — filtered on delivery list
    op.create_index("ix_webhook_deliveries_webhook_id", "webhook_deliveries", ["webhook_id"])
    # audit_logs.user_id + created_at — filtered + sorted together
    op.create_index("ix_audit_logs_user_id_created_at", "audit_logs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_user_id_created_at", "audit_logs")
    op.drop_index("ix_webhook_deliveries_webhook_id", "webhook_deliveries")
    op.drop_index("ix_step_results_pipeline_run_id", "step_results")
    op.drop_index("ix_pipeline_runs_created_at", "pipeline_runs")
    op.drop_index("ix_pipeline_runs_status", "pipeline_runs")

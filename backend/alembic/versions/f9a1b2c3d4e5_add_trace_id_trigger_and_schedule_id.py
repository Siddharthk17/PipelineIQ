"""add_trace_id_trigger_and_schedule_id_to_pipeline_runs

Adds trace_id column to pipeline_runs (trigger and schedule_id already exist).
Handles partial prior application gracefully.

Revision ID: f9a1b2c3d4e5
Revises: f8e0c4e0b13d
Create Date: 2026-05-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "f9a1b2c3d4e5"
down_revision: Union[str, None] = "f8e0c4e0b13d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    cols = [c["name"] for c in inspector.get_columns(table)]
    return column in cols


def upgrade() -> None:
    if not _has_column("pipeline_runs", "trace_id"):
        op.add_column("pipeline_runs", sa.Column("trace_id", sa.String(32), nullable=True))
    if not _has_column("pipeline_runs", "trigger"):
        op.add_column("pipeline_runs", sa.Column("trigger", sa.String(20), nullable=False, server_default="manual"))
    if not _has_column("pipeline_runs", "schedule_id"):
        op.add_column("pipeline_runs", sa.Column("schedule_id", sa.Uuid(), nullable=True))
    if not _has_column("pipeline_runs", "schedule_id"):
        op.create_foreign_key(
            "fk_pipeline_runs_schedule_id",
            "pipeline_runs", "pipeline_schedules",
            ["schedule_id"], ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if _has_column("pipeline_runs", "schedule_id"):
        try:
            op.drop_constraint("fk_pipeline_runs_schedule_id", "pipeline_runs", type_="foreignkey")
        except Exception:
            pass
        op.drop_column("pipeline_runs", "schedule_id")
    if _has_column("pipeline_runs", "trigger"):
        op.drop_column("pipeline_runs", "trigger")
    if _has_column("pipeline_runs", "trace_id"):
        op.drop_column("pipeline_runs", "trace_id")

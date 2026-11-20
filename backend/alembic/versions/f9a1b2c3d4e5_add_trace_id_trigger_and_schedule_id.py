"""add_trace_id_trigger_and_schedule_id_to_pipeline_runs

Adds trace_id column to pipeline_runs (trigger and schedule_id already exist).
Handles partial prior application gracefully.

Revision ID: f9a1b2c3d4e5
Revises: f8e0c4e0b13d
Create Date: 2026-05-22
"""
import contextlib
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
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


def _drop_schedule_id_fk() -> None:
    """Drop whichever FK constrains pipeline_runs.schedule_id.

    The FK was auto-named ``pipeline_runs_schedule_id_fkey`` at creation
    (the old downgrade guessed ``fk_pipeline_runs_schedule_id`` and the
    failed drop was swallowed, wedging every later statement). Introspect
    instead of guessing.
    """
    bind = op.get_bind()
    for const in inspect(bind).get_foreign_keys("pipeline_runs"):
        if const.get("constrained_columns") == ["schedule_id"]:
            with contextlib.suppress(Exception):
                op.drop_constraint(
                    const["name"], "pipeline_runs", type_="foreignkey")
            return


def downgrade() -> None:
    if _has_column("pipeline_runs", "schedule_id"):
        _drop_schedule_id_fk()
        op.drop_column("pipeline_runs", "schedule_id")
    if _has_column("pipeline_runs", "trigger"):
        op.drop_column("pipeline_runs", "trigger")
    if _has_column("pipeline_runs", "trace_id"):
        op.drop_column("pipeline_runs", "trace_id")

"""add healing attempts table

Revision ID: 9f3c1b2a4d5e
Revises: 8a82ac641934
Create Date: 2026-04-20 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9f3c1b2a4d5e"
down_revision: Union[str, None] = "8a82ac641934"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_HEALING_STATUS_VALUES = (
    "CREATED",
    "NON_HEALABLE",
    "AI_INVALID",
    "VALIDATION_FAILED",
    "APPLIED",
    "FAILED",
)


def _healing_status_enum(dialect_name: str) -> sa.Enum:
    if dialect_name == "postgresql":
        from sqlalchemy.dialects import postgresql

        return postgresql.ENUM(
            *_HEALING_STATUS_VALUES,
            name="healingattemptstatus",
            create_type=False,
        )
    return sa.Enum(*_HEALING_STATUS_VALUES, name="healingattemptstatus")


def upgrade() -> None:
    bind = op.get_bind()
    healing_status_enum = _healing_status_enum(bind.dialect.name)
    if bind.dialect.name == "postgresql":
        healing_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "healing_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_run_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", healing_status_enum, nullable=False),
        sa.Column("failed_step_name", sa.String(length=255), nullable=True),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("classification_reason", sa.Text(), nullable=True),
        sa.Column("proposed_yaml", sa.Text(), nullable=True),
        sa.Column("diff_lines", sa.JSON(), nullable=True),
        sa.Column("ai_valid", sa.Boolean(), nullable=True),
        sa.Column("ai_error", sa.Text(), nullable=True),
        sa.Column("parser_valid", sa.Boolean(), nullable=True),
        sa.Column("sandbox_passed", sa.Boolean(), nullable=True),
        sa.Column("validation_errors", sa.JSON(), nullable=True),
        sa.Column("validation_warnings", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pipeline_run_id",
            "attempt_number",
            name="uq_healing_attempts_run_attempt",
        ),
    )
    op.create_index(
        "ix_healing_attempts_pipeline_run_id",
        "healing_attempts",
        ["pipeline_run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_healing_attempts_pipeline_run_id",
        table_name="healing_attempts",
    )
    op.drop_table("healing_attempts")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _healing_status_enum(bind.dialect.name).drop(bind, checkfirst=True)

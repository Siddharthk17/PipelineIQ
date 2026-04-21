"""extend healing runtime contract

Revision ID: c5d6e7f8a9b0
Revises: 9f3c1b2a4d5e
Create Date: 2026-04-21 22:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, None] = "9f3c1b2a4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        for status_value in ("HEALING", "HEALED", "TIMEOUT"):
            op.execute(
                f"ALTER TYPE pipelinestatus ADD VALUE IF NOT EXISTS '{status_value}'"
            )

    with op.batch_alter_table("healing_attempts") as batch_op:
        batch_op.add_column(sa.Column("pipeline_name", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("old_schema", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("new_schema", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("removed_columns", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("added_columns", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("renamed_candidates", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("gemini_patch", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("sandbox_result", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "applied",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )
        batch_op.add_column(sa.Column("confidence", sa.Numeric(5, 4), nullable=True))
        batch_op.add_column(sa.Column("healed_at", sa.DateTime(timezone=True), nullable=True))

    op.create_index(
        "ix_healing_attempts_applied",
        "healing_attempts",
        ["applied"],
        unique=False,
    )

    if bind.dialect.name == "postgresql":
        op.execute("DROP RULE IF EXISTS healing_attempts_no_delete ON healing_attempts")
        op.execute("DROP RULE IF EXISTS healing_attempts_no_update ON healing_attempts")
        op.execute(
            """
            CREATE RULE healing_attempts_no_delete AS
            ON DELETE TO healing_attempts
            DO INSTEAD NOTHING
            """
        )
        op.execute(
            """
            CREATE RULE healing_attempts_no_update AS
            ON UPDATE TO healing_attempts
            DO INSTEAD NOTHING
            """
        )


def downgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        op.execute("DROP RULE IF EXISTS healing_attempts_no_update ON healing_attempts")
        op.execute("DROP RULE IF EXISTS healing_attempts_no_delete ON healing_attempts")

    op.drop_index("ix_healing_attempts_applied", table_name="healing_attempts")

    with op.batch_alter_table("healing_attempts") as batch_op:
        batch_op.drop_column("healed_at")
        batch_op.drop_column("confidence")
        batch_op.drop_column("applied")
        batch_op.drop_column("sandbox_result")
        batch_op.drop_column("gemini_patch")
        batch_op.drop_column("renamed_candidates")
        batch_op.drop_column("added_columns")
        batch_op.drop_column("removed_columns")
        batch_op.drop_column("new_schema")
        batch_op.drop_column("old_schema")
        batch_op.drop_column("pipeline_name")

"""add_file_profiles_table

Revision ID: 0008
Revises: b2c3d4e5f6a7
Create Date: 2026-04-05

Creates the file_profiles table for storing automatic data profiling results.
Stores column-level statistics, semantic types, and quality flags.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "file_profiles",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("file_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "computed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("profile", JSONB(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("col_count", sa.Integer(), nullable=False),
        sa.Column("completeness_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["file_id"], ["uploaded_files.id"], ondelete="CASCADE"),
    )

    op.create_index(
        "idx_file_profiles_file_id",
        "file_profiles",
        ["file_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_file_profiles_file_id")
    op.drop_table("file_profiles")

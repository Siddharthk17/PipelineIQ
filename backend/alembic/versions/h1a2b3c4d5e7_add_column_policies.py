"""Add column_policies table for column-level access security.

Revision ID: h1a2b3c4d5e7
Revises: h1a2b3c4d5e6
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, ARRAY


revision: str = "h1a2b3c4d5e7"
down_revision: Union[str, None] = "h1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "column_policies",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "file_id",
            UUID(as_uuid=True),
            sa.ForeignKey("uploaded_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("column_name", sa.String(500), nullable=False),
        sa.Column("policy", sa.String(20), nullable=False),
        sa.Column("mask_pattern", sa.String(100), nullable=True),
        sa.Column(
            "allowed_roles",
            ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )

    op.create_check_constraint(
        "ck_column_policies_policy",
        "column_policies",
        "policy IN ('redacted', 'masked')",
    )
    op.create_unique_constraint(
        "uq_column_policies_file_column",
        "column_policies",
        ["file_id", "column_name"],
    )
    op.create_index(
        "idx_column_policies_file", "column_policies", ["file_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_column_policies_file")
    op.drop_table("column_policies")

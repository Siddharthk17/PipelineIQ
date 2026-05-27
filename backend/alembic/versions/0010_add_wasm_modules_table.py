"""add_wasm_modules_table

Revision ID: 0010
Revises: b315b6809272
Create Date: 2026-05-16

Creates the wasm_modules table for storing registered WebAssembly UDF modules.
Modules are stored in MinIO/S3 with metadata tracked in this table.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010"
down_revision = "b315b6809272"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wasm_modules",
        sa.Column("id", sa.Uuid, primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("storage_key", sa.String(512), unique=True, nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256_hash", sa.String(64), nullable=False, index=True),
        sa.Column("exports", JSONB, nullable=False),
        sa.Column("imports", JSONB, nullable=False),
        sa.Column(
            "fuel_budget",
            sa.Integer,
            nullable=False,
            server_default="10000000",
        ),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default="true",
            default=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("wasm_modules")

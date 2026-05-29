"""add_wasm_pipeline_usage

Revision ID: 0011
Revises: j1a2b3c4d5e9
Create Date: 2026-05-29

Adds pipeline_usage_count to wasm_modules so modules in active use
cannot be deleted. The count is incremented when a pipeline YAML
references the module and decremented when the reference is removed.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011"
down_revision: Union[str, None] = "j1a2b3c4d5e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "wasm_modules",
        sa.Column(
            "pipeline_usage_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index(
        "idx_wasm_modules_usage_count",
        "wasm_modules",
        ["pipeline_usage_count"],
    )


def downgrade() -> None:
    op.drop_index("idx_wasm_modules_usage_count")
    op.drop_column("wasm_modules", "pipeline_usage_count")

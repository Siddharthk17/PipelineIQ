"""Add output_schema JSONB column to pipeline_contracts.

Revision ID: 0015_add_output_schema_to_contracts
Revises: 0014_otel_and_contracts
Create Date: 2026-05-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0015"
down_revision: Union[str, None] = "1a158ef49016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pipeline_contracts
            ADD COLUMN IF NOT EXISTS output_schema JSONB
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE pipeline_contracts
            DROP COLUMN IF EXISTS output_schema
    """)

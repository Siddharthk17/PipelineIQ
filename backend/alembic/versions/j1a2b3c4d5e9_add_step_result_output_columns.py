"""add_step_result_output_columns

Revision ID: j1a2b3c4d5e9
Revises: 874a4ad78177
Create Date: 2026-05-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'j1a2b3c4d5e9'
down_revision: Union[str, None] = '874a4ad78177'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='step_results' AND column_name='download_url') THEN
                ALTER TABLE step_results ADD COLUMN download_url TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='step_results' AND column_name='output_filename') THEN
                ALTER TABLE step_results ADD COLUMN output_filename VARCHAR(500);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='step_results' AND column_name='output_format') THEN
                ALTER TABLE step_results ADD COLUMN output_format VARCHAR(20);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='step_results' AND column_name='output_object_name') THEN
                ALTER TABLE step_results ADD COLUMN output_object_name VARCHAR(1000);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='step_results' AND column_name='row_count_out') THEN
                ALTER TABLE step_results ADD COLUMN row_count_out INTEGER;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='step_results' AND column_name='output_size_bytes') THEN
                ALTER TABLE step_results ADD COLUMN output_size_bytes INTEGER;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE step_results DROP COLUMN IF EXISTS output_size_bytes")
    op.execute("ALTER TABLE step_results DROP COLUMN IF EXISTS row_count_out")
    op.execute("ALTER TABLE step_results DROP COLUMN IF EXISTS output_object_name")
    op.execute("ALTER TABLE step_results DROP COLUMN IF EXISTS output_format")
    op.execute("ALTER TABLE step_results DROP COLUMN IF EXISTS output_filename")
    op.execute("ALTER TABLE step_results DROP COLUMN IF EXISTS download_url")

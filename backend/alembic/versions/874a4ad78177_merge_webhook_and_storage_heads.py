"""merge_webhook_and_storage_heads

Revision ID: 874a4ad78177
Revises: 0bc02f8e7589, i1a2b3c4d5e8
Create Date: 2026-05-26 14:25:18.966962

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '874a4ad78177'
down_revision: Union[str, None] = ('0bc02f8e7589', 'i1a2b3c4d5e8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

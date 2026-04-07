"""add_index_to_uploaded_files_original_filename

Revision ID: 8a82ac641934
Revises: cb3e11c88344
Create Date: 2026-04-06 20:13:40.172420

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8a82ac641934"
down_revision: Union[str, None] = "cb3e11c88344"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_uploaded_files_original_filename",
        "uploaded_files",
        ["original_filename"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_uploaded_files_original_filename", table_name="uploaded_files")

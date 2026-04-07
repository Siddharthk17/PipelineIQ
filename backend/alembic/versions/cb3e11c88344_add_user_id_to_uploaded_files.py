"""add_user_id_to_uploaded_files

Revision ID: cb3e11c88344
Revises: 0009
Create Date: 2026-04-06 09:53:00.476632

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "cb3e11c88344"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_UPLOADED_FILES_USER_ID_USERS = "fk_uploaded_files_user_id_users"


def upgrade() -> None:
    # Add user_id as nullable first to handle existing data
    op.add_column("uploaded_files", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        FK_UPLOADED_FILES_USER_ID_USERS,
        "uploaded_files",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Assign existing files to the first available user
    connection = op.get_bind()
    user_id_res = connection.execute(sa.text("SELECT id FROM users LIMIT 1")).fetchone()
    if user_id_res:
        user_id = user_id_res[0]
        connection.execute(
            sa.text(
                "UPDATE uploaded_files SET user_id = :user_id WHERE user_id IS NULL"
            ),
            {"user_id": user_id},
        )

    # Now set to nullable=False
    op.alter_column("uploaded_files", "user_id", nullable=False)


def downgrade() -> None:
    op.drop_constraint(
        FK_UPLOADED_FILES_USER_ID_USERS, "uploaded_files", type_="foreignkey"
    )
    op.drop_column("uploaded_files", "user_id")

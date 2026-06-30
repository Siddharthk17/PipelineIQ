"""tenant scope pipeline versions

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_UNIQUE = "pipeline_versions_pipeline_name_version_number_key"
NEW_UNIQUE = "uq_pipeline_versions_user_pipeline_version"


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.add_column(
            "pipeline_versions",
            sa.Column("user_id", sa.Uuid(), nullable=True),
        )
        op.create_foreign_key(
            "pipeline_versions_user_id_fkey",
            "pipeline_versions",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.execute(
            """
            UPDATE pipeline_versions pv
            SET user_id = pr.user_id
            FROM pipeline_runs pr
            WHERE pv.run_id = pr.id
              AND pv.user_id IS NULL
            """
        )
        op.execute(
            f"ALTER TABLE pipeline_versions DROP CONSTRAINT IF EXISTS {OLD_UNIQUE}"
        )
        op.create_unique_constraint(
            NEW_UNIQUE,
            "pipeline_versions",
            ["user_id", "pipeline_name", "version_number"],
        )
        op.create_index(
            "ix_pipeline_versions_user_id",
            "pipeline_versions",
            ["user_id"],
        )
        return

    with op.batch_alter_table("pipeline_versions", recreate="always") as batch:
        batch.add_column(sa.Column("user_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "pipeline_versions_user_id_fkey",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint(
            NEW_UNIQUE,
            ["user_id", "pipeline_name", "version_number"],
        )
        batch.create_index("ix_pipeline_versions_user_id", ["user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.drop_index("ix_pipeline_versions_user_id", table_name="pipeline_versions")
        op.drop_constraint(NEW_UNIQUE, "pipeline_versions", type_="unique")
        op.create_unique_constraint(
            OLD_UNIQUE,
            "pipeline_versions",
            ["pipeline_name", "version_number"],
        )
        op.drop_constraint(
            "pipeline_versions_user_id_fkey",
            "pipeline_versions",
            type_="foreignkey",
        )
        op.drop_column("pipeline_versions", "user_id")
        return

    with op.batch_alter_table("pipeline_versions", recreate="always") as batch:
        batch.drop_index("ix_pipeline_versions_user_id")
        batch.drop_constraint(NEW_UNIQUE, type_="unique")
        batch.drop_constraint("pipeline_versions_user_id_fkey", type_="foreignkey")
        batch.drop_column("user_id")

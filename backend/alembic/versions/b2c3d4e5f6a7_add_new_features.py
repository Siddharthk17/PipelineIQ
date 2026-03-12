"""add_new_features

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-15

Creates tables for pipeline schedules, notification configs, and
pipeline permissions. Adds CANCELLED to PipelineStatus enum.
Adds version and previous_version_id columns to uploaded_files.
"""

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # --- Add CANCELLED to PipelineStatus enum ---
    # PostgreSQL requires ALTER TYPE to add a new enum value
    op.execute("ALTER TYPE pipelinestatus ADD VALUE IF NOT EXISTS 'CANCELLED'")

    # --- Add version and previous_version_id to uploaded_files ---
    uploaded_columns = {column["name"] for column in inspector.get_columns("uploaded_files")}
    if "version" not in uploaded_columns:
        op.add_column(
            "uploaded_files",
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        )
        uploaded_columns.add("version")

    if "previous_version_id" not in uploaded_columns:
        op.add_column(
            "uploaded_files",
            sa.Column("previous_version_id", sa.Uuid(), nullable=True),
        )
        uploaded_columns.add("previous_version_id")

    uploaded_fks = {fk["name"] for fk in inspector.get_foreign_keys("uploaded_files")}
    if "fk_uploaded_files_previous_version_id" not in uploaded_fks:
        op.create_foreign_key(
            "fk_uploaded_files_previous_version_id",
            "uploaded_files",
            "uploaded_files",
            ["previous_version_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # --- Create pipeline_schedules table ---
    if "pipeline_schedules" not in existing_tables:
        op.create_table(
            "pipeline_schedules",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("pipeline_name", sa.String(255), nullable=False),
            sa.Column("yaml_config", sa.Text(), nullable=False),
            sa.Column("cron_expression", sa.String(100), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    # --- Create notification_configs table ---
    from sqlalchemy.dialects import postgresql

    # Use create_type=False for Postgres to avoid 'type already exists' error in create_table
    if op.get_bind().dialect.name == "postgresql":
        notification_type = postgresql.ENUM("slack", "email", name="notificationtype", create_type=False)
        notification_type.create(op.get_bind(), checkfirst=True)
    else:
        notification_type = sa.Enum("slack", "email", name="notificationtype")

    if "notification_configs" not in existing_tables:
        op.create_table(
            "notification_configs",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("type", notification_type, nullable=False),
            sa.Column("config", sa.dialects.postgresql.JSONB(), nullable=False),
            sa.Column("events", sa.dialects.postgresql.JSONB(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    # --- Create pipeline_permissions table ---
    if op.get_bind().dialect.name == "postgresql":
        permission_level = postgresql.ENUM("owner", "runner", "viewer", name="permissionlevel", create_type=False)
        permission_level.create(op.get_bind(), checkfirst=True)
    else:
        permission_level = sa.Enum("owner", "runner", "viewer", name="permissionlevel")

    if "pipeline_permissions" not in existing_tables:
        op.create_table(
            "pipeline_permissions",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("pipeline_name", sa.String(255), nullable=False, index=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("permission_level", permission_level, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("pipeline_name", "user_id"),
        )


def downgrade() -> None:
    op.drop_table("pipeline_permissions")
    op.execute("DROP TYPE IF EXISTS permissionlevel")

    op.drop_table("notification_configs")
    op.execute("DROP TYPE IF EXISTS notificationtype")

    op.drop_table("pipeline_schedules")

    op.drop_constraint("fk_uploaded_files_previous_version_id", "uploaded_files", type_="foreignkey")
    op.drop_column("uploaded_files", "previous_version_id")
    op.drop_column("uploaded_files", "version")

    # Note: PostgreSQL does not support removing enum values.
    # CANCELLED will remain in the pipelinestatus type.

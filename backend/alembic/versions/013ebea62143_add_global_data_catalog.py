"""Add data_assets and asset_relationships tables for global data mesh

Revision ID: 013ebea62143
Revises: 9f1445650ed1
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, TIMESTAMP


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "data_assets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("asset_type", sa.String(20), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("namespace", sa.String(500), nullable=False),
        sa.Column("metadata", JSONB(), nullable=True, server_default="{}"),
        sa.Column("owner_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("last_seen_at", TIMESTAMP(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_unique_constraint(
        "uq_data_assets_type_ns_name",
        "data_assets",
        ["asset_type", "namespace", "name"],
    )

    op.create_check_constraint(
        "ck_data_assets_type",
        "data_assets",
        "asset_type IN ('file', 'column', 'pipeline', 'topic')",
    )

    op.create_index(
        "idx_data_assets_name_trgm",
        "data_assets",
        ["name"],
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )

    op.create_index("idx_data_assets_type", "data_assets", ["asset_type"])
    op.create_index("idx_data_assets_owner", "data_assets", ["owner_id"])

    op.create_table(
        "asset_relationships",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_id", UUID(as_uuid=True),
                  sa.ForeignKey("data_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True),
                  sa.ForeignKey("data_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation", sa.String(20), nullable=False),
        sa.Column("pipeline_name", sa.String(500), nullable=True),
        sa.Column("run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_check_constraint(
        "ck_asset_rel_relation",
        "asset_relationships",
        "relation IN ('reads_from', 'writes_to', 'transforms', 'joins')",
    )

    op.create_index("idx_asset_rel_source", "asset_relationships", ["source_id"])
    op.create_index("idx_asset_rel_target", "asset_relationships", ["target_id"])
    op.create_index(
        "idx_asset_rel_source_pipeline",
        "asset_relationships",
        ["source_id", "pipeline_name"],
    )


def downgrade() -> None:
    op.drop_index("idx_asset_rel_source_pipeline")
    op.drop_index("idx_asset_rel_target")
    op.drop_index("idx_asset_rel_source")
    op.drop_table("asset_relationships")
    op.drop_index("idx_data_assets_owner")
    op.drop_index("idx_data_assets_type")
    op.drop_index("idx_data_assets_name_trgm")
    op.drop_table("data_assets")

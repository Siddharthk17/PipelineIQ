"""Add storage_events table for Arrow bus tier tracking.

Revision ID: i1a2b3c4d5e8
Revises: h1a2b3c4d5e7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP

revision: str = "i1a2b3c4d5e8"
down_revision: Union[str, None] = "h1a2b3c4d5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "storage_events",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_id",    sa.String(100), nullable=True),
        sa.Column("step_name", sa.String(500), nullable=True),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("tier",       sa.String(10), nullable=False),
        sa.Column("payload_bytes", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms",   sa.Integer(),   nullable=True),
        sa.Column("object_name",   sa.String(1000), nullable=True),
        sa.Column(
            "created_at", TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"), nullable=False,
        ),
    )

    op.create_check_constraint(
        "ck_storage_event_type",
        "storage_events",
        "event_type IN ('write_hot','write_warm','write_cold',"
        "'read_hot','read_warm','read_cold',"
        "'evict_hot_to_warm','evict_warm_to_cold',"
        "'cleanup_run','lifecycle_expire')",
    )
    op.create_check_constraint(
        "ck_storage_tier",
        "storage_events",
        "tier IN ('hot','warm','cold')",
    )

    op.create_index("idx_storage_events_run",  "storage_events", ["run_id"])
    op.create_index("idx_storage_events_time", "storage_events", ["created_at"])
    op.create_index("idx_storage_events_tier", "storage_events", ["tier", "event_type"])


def downgrade() -> None:
    op.drop_index("idx_storage_events_tier")
    op.drop_index("idx_storage_events_time")
    op.drop_index("idx_storage_events_run")
    op.drop_table("storage_events")

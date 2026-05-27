"""Add append-only RULEs to webhook_deliveries and missing columns.

Revision ID: 0bc02f8e7589
Revises: h1a2b3c4d5e7
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP


revision: str = "0bc02f8e7589"
down_revision: Union[str, None] = "h1a2b3c4d5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "webhooks",
        sa.Column("name", sa.String(200), nullable=True),
    )
    op.execute("UPDATE webhooks SET name = 'Webhook ' || substring(id::text, 1, 8)")
    op.alter_column("webhooks", "name", nullable=False)

    op.add_column(
        "webhook_deliveries",
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )

    op.create_index(
        "idx_deliveries_webhook", "webhook_deliveries", ["webhook_id"]
    )
    op.create_index(
        "idx_deliveries_success", "webhook_deliveries", ["response_status"]
    )
    op.create_index(
        "idx_webhooks_owner", "webhooks", ["user_id"]
    )
    op.create_index(
        "idx_webhooks_active", "webhooks", ["is_active"]
    )

    op.execute("""
        CREATE RULE webhook_deliveries_no_delete
        AS ON DELETE TO webhook_deliveries DO INSTEAD NOTHING
    """)
    op.execute("""
        CREATE RULE webhook_deliveries_no_update
        AS ON UPDATE TO webhook_deliveries DO INSTEAD NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP RULE IF EXISTS webhook_deliveries_no_update ON webhook_deliveries")
    op.execute("DROP RULE IF EXISTS webhook_deliveries_no_delete ON webhook_deliveries")

    op.drop_index("idx_webhooks_active")
    op.drop_index("idx_webhooks_owner")
    op.drop_index("idx_deliveries_success")
    op.drop_index("idx_deliveries_webhook")

    op.drop_column("webhook_deliveries", "duration_ms")
    op.drop_column("webhooks", "name")

"""Make contract_violations append-only like the other audit tables.

Revision ID: k1a2b3c4d5e0
Revises: j1a2b3c4d5e9
Create Date: 2026-11-04 00:00:00.000000

webhook_deliveries and healing_attempts already carry DO INSTEAD NOTHING
RULEs (and audit_logs has an immutability trigger). contract_violations had
no such protection, so breach records could be updated or deleted. This
migration closes that gap with the same RULE pattern.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "k1a2b3c4d5e0"
down_revision: Union[str, None] = "j1a2b3c4d5e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE RULE contract_violations_no_delete
        AS ON DELETE TO contract_violations DO INSTEAD NOTHING
    """)
    op.execute("""
        CREATE RULE contract_violations_no_update
        AS ON UPDATE TO contract_violations DO INSTEAD NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP RULE IF EXISTS contract_violations_no_update ON contract_violations")
    op.execute("DROP RULE IF EXISTS contract_violations_no_delete ON contract_violations")

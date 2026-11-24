"""Add CONTRACT_VIOLATION to the pipelinestatus enum (M-083).

Revision ID: 0017_contract_violation_status
Revises: a6fc98d90644
Create Date: 2026-11-17

The Python ``PipelineStatus`` enum and the API-startup hook
(``database.ensure_pipeline_status_values``) already know
CONTRACT_VIOLATION, but no migration ever added it to the Postgres
``pipelinestatus`` type: worker-first deploys (workers run before the
API hook ever fires) break when persisting that status. This migration
owns the value at the schema level.

Postgres path: ``ALTER TYPE ... ADD VALUE`` cannot run inside a
transaction block, so the value is committed outside Alembic's
migration transaction (same COMMIT/ADD/BEGIN dance as the startup
hook). ``IF NOT EXISTS`` keeps re-runs idempotent. Behind pgbouncer in
transaction-pooling mode this statement cannot run at all — the API
startup hook remains as the runtime backstop.

SQLite path: no-op. SQLite stores the enum as VARCHAR with no
database-level value list, so the Python enum change needs no schema
change; the early return keeps ``upgrade head`` green on SQLite.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_contract_violation_status"
down_revision: Union[str, None] = "a6fc98d90644"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite (and any other non-Postgres backend): nothing to do.
        return
    # ALTER TYPE ... ADD VALUE cannot execute inside a transaction block.
    bind.execute(sa.text("COMMIT"))
    try:
        bind.execute(
            sa.text(
                "ALTER TYPE pipelinestatus "
                "ADD VALUE IF NOT EXISTS 'CONTRACT_VIOLATION'"
            )
        )
    finally:
        bind.execute(sa.text("BEGIN"))


def downgrade() -> None:
    # Postgres cannot drop an enum value that may be referenced by rows;
    # removing it would require recreating the type and rewriting every
    # dependent column. The value is inert for pre-upgrade code (no rows
    # carry it unless the new code path ran), so downgrade is a no-op.
    pass

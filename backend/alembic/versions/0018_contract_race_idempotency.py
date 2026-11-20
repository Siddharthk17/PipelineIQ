"""harden contract writes, single-owner claim, idempotency keys.

Covers M-007 (first-run claim race), M-008 (contract create/update race)
and M-062 (idempotency key store):

- pipeline_permissions: partial unique index enforcing a single OWNER row
  per pipeline_name. (The (pipeline_name, user_id) unique constraint
  already exists from 1a158ef49016 and is NOT recreated here.)
- pipeline_contracts: unique (pipeline_name, version) plus a partial
  unique index allowing at most one active row per pipeline_name.
- idempotency_keys: key->result store for run/contract creates.

Revision ID: 0018_contract_race_idempotency
Revises: 0017_contract_violation_status
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_contract_race_idempotency"
down_revision: Union[str, None] = "0017_contract_violation_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    dialect = op.get_context().dialect.name

    if dialect == "postgresql":
        op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # M-007: exactly one OWNER permission row per pipeline name. Concurrent
    # first-run claims collapse to a single winner via IntegrityError.
    op.create_index(
        "uq_pipeline_permissions_owner_name",
        "pipeline_permissions",
        ["pipeline_name"],
        unique=True,
        postgresql_where=sa.text("permission_level = 'owner'"),
        sqlite_where=sa.text("permission_level = 'owner'"),
    )

    # M-008: no duplicate versions, at most one active contract per pipeline.
    op.create_unique_constraint(
        "uq_pipeline_contracts_name_version",
        "pipeline_contracts",
        ["pipeline_name", "version"],
    )
    op.create_index(
        "uq_pipeline_contracts_active_name",
        "pipeline_contracts",
        ["pipeline_name"],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE"),
        sqlite_where=sa.text("is_active"),
    )

    # M-062: idempotency key -> completed-response store.
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB, UUID

        id_type = UUID(as_uuid=True)
        json_type = JSONB
        id_default = sa.text("gen_random_uuid()")
    else:
        id_type = sa.Uuid()
        json_type = sa.JSON()
        id_default = None

    op.create_table(
        "idempotency_keys",
        sa.Column("id", id_type, primary_key=True, server_default=id_default),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column(
            "user_id",
            id_type,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status_code", sa.Integer, nullable=True),
        sa.Column("response_body", json_type, nullable=True),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "scope", "key", "user_id",
            name="uq_idempotency_keys_scope_key_user",
        ),
    )
    op.create_index(
        "ix_idempotency_keys_scope_key_user",
        "idempotency_keys",
        ["scope", "key", "user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_idempotency_keys_scope_key_user", table_name="idempotency_keys"
    )
    op.drop_table("idempotency_keys")
    op.drop_index(
        "uq_pipeline_contracts_active_name", table_name="pipeline_contracts"
    )
    op.drop_constraint(
        "uq_pipeline_contracts_name_version",
        "pipeline_contracts",
        type_="unique",
    )
    op.drop_index(
        "uq_pipeline_permissions_owner_name",
        table_name="pipeline_permissions",
    )

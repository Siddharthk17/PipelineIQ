"""add_contract_severity_and_consumers

Adds severity (warn/block) and consumers (JSONB list) columns to
pipeline_contracts. Severity controls whether a breach blocks the run
or merely logs a warning. Consumers stores notification targets
(emails, Slack webhooks) for breach alerts.

Revision ID: f8e0c4e0b13d
Revises: 0014_otel_and_contracts
Create Date: 2026-05-21 15:36:44.327802
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f8e0c4e0b13d"
down_revision: Union[str, None] = "0014_otel_and_contracts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    dialect = op.get_context().dialect.name
    if dialect == "postgresql":
        op.execute("CREATE TYPE contractseverity AS ENUM ('warn', 'block')")

    op.add_column(
        "pipeline_contracts",
        sa.Column(
            "severity",
            sa.String(10),
            nullable=False,
            server_default="warn",
        ),
    )

    op.add_column(
        "pipeline_contracts",
        sa.Column(
            "consumers",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )

    if dialect != "postgresql":
        op.create_check_constraint(
            "ck_pipeline_contracts_severity",
            "pipeline_contracts",
            "severity IN ('warn', 'block')",
        )


def downgrade() -> None:
    dialect = op.get_context().dialect.name

    op.drop_column("pipeline_contracts", "consumers")
    op.drop_column("pipeline_contracts", "severity")

    if dialect != "postgresql":
        op.drop_constraint(
            "ck_pipeline_contracts_severity",
            "pipeline_contracts",
            type_="check",
        )

    if dialect == "postgresql":
        op.execute("DROP TYPE IF EXISTS contractseverity")

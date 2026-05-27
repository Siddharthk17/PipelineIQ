"""Add OTel tracing columns to step_results and data contract tables.

Revision ID: 0014_otel_and_contracts
Revises: 013ebea62143
Create Date: 2026-05-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, TIMESTAMP

revision: str = "0014_otel_and_contracts"
down_revision: Union[str, None] = "013ebea62143"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CONTRACT_VIOLATION enum value is added by database.ensure_pipeline_status_values()
    # at application startup rather than here, because ALTER TYPE … ADD VALUE cannot
    # run inside a transaction — it fails silently behind pgbouncer transaction pooling.

    # ── OTel columns on step_results ───────────────────────────────────────
    # trace_id, span_id: link each step to its OpenTelemetry span in Jaeger
    # started_at, completed_at: per-step wall-clock timing for Gantt chart
    # engine: which execution engine ran the step (duckdb/pandas/wasm/io)
    op.execute("""
        ALTER TABLE step_results
            ADD COLUMN IF NOT EXISTS trace_id     VARCHAR(32),
            ADD COLUMN IF NOT EXISTS span_id      VARCHAR(16),
            ADD COLUMN IF NOT EXISTS started_at   TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS engine       VARCHAR(20)
    """)

    # ── pipeline_contracts ─────────────────────────────────────────────────
    op.create_table(
        "pipeline_contracts",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("pipeline_name", sa.String(255), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("yaml_content", sa.Text(), nullable=False),
        sa.Column(
            "user_id",
            UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )

    # ── contract_violations ────────────────────────────────────────────────
    op.create_table(
        "contract_violations",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "run_id",
            UUID(as_uuid=False),
            sa.ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("step_name", sa.String(255), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(50), nullable=False),
        sa.Column("column", sa.String(255), nullable=False),
        sa.Column("rule", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("actual", sa.Text(), nullable=True),
        sa.Column("expected", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )

    op.create_index(
        "ix_contract_violations_run_step",
        "contract_violations",
        ["run_id", "step_name"],
    )
    op.create_index(
        "ix_contract_violations_severity",
        "contract_violations",
        ["run_id", "severity"],
    )


def downgrade() -> None:
    op.drop_index("ix_contract_violations_severity")
    op.drop_index("ix_contract_violations_run_step")
    op.drop_table("contract_violations")
    op.drop_table("pipeline_contracts")

    op.execute("""
        ALTER TABLE step_results
            DROP COLUMN IF EXISTS trace_id,
            DROP COLUMN IF EXISTS span_id,
            DROP COLUMN IF EXISTS started_at,
            DROP COLUMN IF EXISTS completed_at,
            DROP COLUMN IF EXISTS engine
    """)

"""add_streaming_stats

Revision ID: 9f1445650ed1
Revises: 0010
Create Date: 2026-05-18 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP

# revision identifiers, used by Alembic.
revision = '9f1445650ed1'
down_revision = '0010'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'streaming_stats',
        sa.Column('run_id', UUID(as_uuid=True),
                  sa.ForeignKey('pipeline_runs.id', ondelete='CASCADE'),
                  primary_key=True),
        sa.Column('batches_processed',   sa.Integer(),    nullable=False, server_default='0'),
        sa.Column('messages_processed',  sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('messages_failed',     sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('messages_dlq',        sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('throughput_per_sec',  sa.Float(),      nullable=True),
        sa.Column('consumer_lag',        sa.BigInteger(), nullable=True),
        sa.Column('last_batch_at',       TIMESTAMP(timezone=True), nullable=True),
        sa.Column('started_at',          TIMESTAMP(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),
        sa.Column('topic',               sa.String(500), nullable=True),
        sa.Column('consumer_group',      sa.String(500), nullable=True),
        sa.Column('avg_batch_latency_ms',sa.Float(),     nullable=True),
    )

    # Add streaming status values to pipeline_runs enum
    # Note: ALTER TYPE ... ADD VALUE cannot be run in a transaction.
    # Alembic usually runs in a transaction. We use op.execute to run it.
    op.execute("ALTER TYPE pipelinestatus ADD VALUE IF NOT EXISTS 'STREAMING_ACTIVE'")
    op.execute("ALTER TYPE pipelinestatus ADD VALUE IF NOT EXISTS 'STREAMING_PAUSED'")
    op.execute("ALTER TYPE pipelinestatus ADD VALUE IF NOT EXISTS 'STREAMING_STOPPED'")


def downgrade() -> None:
    op.drop_table('streaming_stats')

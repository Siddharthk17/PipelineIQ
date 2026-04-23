"""Add pipeline_schedules and schedule_runs tables

Revision ID: 29058ddf0eae
Revises: c5d6e7f8a9b0
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP

# revision identifiers, used by Alembic.
revision = '29058ddf0eae'
down_revision = 'c5d6e7f8a9b0'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Create schedule_runs table
    op.create_table(
        'schedule_runs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('schedule_id', UUID(as_uuid=True),
                  sa.ForeignKey('pipeline_schedules.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('run_id', UUID(as_uuid=True),
                  sa.ForeignKey('pipeline_runs.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('triggered_at', TIMESTAMP(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),
        sa.Column('status', sa.String(20), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
    )

    op.create_index('idx_schedule_runs_schedule_id',
                     'schedule_runs', ['schedule_id', 'triggered_at'])

    # Add trigger + schedule_id to pipeline_runs
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='pipeline_runs' AND column_name='trigger'
            ) THEN
                ALTER TABLE pipeline_runs
                    ADD COLUMN trigger VARCHAR(20) DEFAULT 'manual',
                    ADD COLUMN schedule_id UUID REFERENCES pipeline_schedules(id) ON DELETE SET NULL;
            END IF;
        END $$;
    """)

    # Add missing columns to pipeline_schedules
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='pipeline_schedules' AND column_name='cron_human') THEN
                ALTER TABLE pipeline_schedules ADD COLUMN cron_human VARCHAR(200);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='pipeline_schedules' AND column_name='last_run_status') THEN
                ALTER TABLE pipeline_schedules ADD COLUMN last_run_status VARCHAR(20);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='pipeline_schedules' AND column_name='total_runs') THEN
                ALTER TABLE pipeline_schedules ADD COLUMN total_runs INTEGER NOT NULL DEFAULT 0;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='pipeline_schedules' AND column_name='successful_runs') THEN
                ALTER TABLE pipeline_schedules ADD COLUMN successful_runs INTEGER NOT NULL DEFAULT 0;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='pipeline_schedules' AND column_name='failed_runs') THEN
                ALTER TABLE pipeline_schedules ADD COLUMN failed_runs INTEGER NOT NULL DEFAULT 0;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='pipeline_schedules' AND column_name='healed_runs') THEN
                ALTER TABLE pipeline_schedules ADD COLUMN healed_runs INTEGER NOT NULL DEFAULT 0;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='pipeline_schedules' AND column_name='updated_at') THEN
                ALTER TABLE pipeline_schedules ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();
            END IF;
        END $$;
    """)

    # Index for active schedules (queried by Celery Beat)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_pipeline_schedules_active 
        ON pipeline_schedules (is_active, next_run_at) 
        WHERE is_active = true;
    """)

    # Index for listing by user
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_pipeline_schedules_user 
        ON pipeline_schedules (user_id, created_at);
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN IF EXISTS schedule_id")
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN IF EXISTS trigger")
    op.drop_index('idx_schedule_runs_schedule_id')
    op.drop_table('schedule_runs')
    op.execute("DROP INDEX IF EXISTS idx_pipeline_schedules_user")
    op.execute("DROP INDEX IF EXISTS idx_pipeline_schedules_active")
    # Note: We don't drop pipeline_schedules table because it already existed before this migration

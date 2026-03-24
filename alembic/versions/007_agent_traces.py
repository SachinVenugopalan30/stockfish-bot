"""Add agent_reasoning_traces table and agent_trace_id FK on trades

Revision ID: 007
Revises: 006
Create Date: 2026-03-22
"""
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use IF NOT EXISTS so this is safe whether or not create_all already ran
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_reasoning_traces (
            id SERIAL PRIMARY KEY,
            ticker VARCHAR(10) NOT NULL,
            tool_calls JSON NOT NULL DEFAULT '[]',
            total_tokens INTEGER,
            duration_ms INTEGER,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_agent_traces_ticker
        ON agent_reasoning_traces (ticker)
    """)
    op.execute("""
        ALTER TABLE trades
        ADD COLUMN IF NOT EXISTS agent_trace_id INTEGER
        REFERENCES agent_reasoning_traces(id) ON DELETE SET NULL
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE trades DROP COLUMN IF EXISTS agent_trace_id")
    op.execute("DROP INDEX IF EXISTS ix_agent_traces_ticker")
    op.execute("DROP TABLE IF EXISTS agent_reasoning_traces")

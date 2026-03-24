"""Add signal_strength to trades and create decision_outcomes table

Revision ID: 008
Revises: 007
Create Date: 2026-03-23
"""
from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add signal_strength column to trades (IF NOT EXISTS for idempotency)
    op.execute("""
        ALTER TABLE trades
        ADD COLUMN IF NOT EXISTS signal_strength NUMERIC(6, 4)
    """)

    # Create decision_outcomes table
    op.execute("""
        CREATE TABLE IF NOT EXISTS decision_outcomes (
            id SERIAL PRIMARY KEY,
            trade_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
            ticker VARCHAR(10) NOT NULL,
            action VARCHAR(4) NOT NULL,
            confidence NUMERIC(4, 3),
            price_at_decision NUMERIC(18, 6),
            signal_strength NUMERIC(6, 4),
            price_at_1h NUMERIC(18, 6),
            price_at_24h NUMERIC(18, 6),
            pct_change_1h NUMERIC(8, 4),
            pct_change_24h NUMERIC(8, 4),
            outcome_correct_1h BOOLEAN,
            outcome_correct_24h BOOLEAN,
            decided_at TIMESTAMP WITH TIME ZONE NOT NULL,
            evaluated_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_decision_outcomes_trade_id
        ON decision_outcomes (trade_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_decision_outcomes_ticker
        ON decision_outcomes (ticker)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_decision_outcomes_ticker")
    op.execute("DROP INDEX IF EXISTS ix_decision_outcomes_trade_id")
    op.execute("DROP TABLE IF EXISTS decision_outcomes")
    op.execute("ALTER TABLE trades DROP COLUMN IF EXISTS signal_strength")

"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-22 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ trades
    op.create_table(
        "trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("action", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("price_at_exec", sa.Numeric(18, 6), nullable=True),
        sa.Column("entry_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("exit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(18, 6), nullable=True),
        sa.Column("hold_duration", sa.Interval(), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("trigger_type", sa.String(10), nullable=True),
        sa.Column("trigger_detail", sa.Text(), nullable=True),
        sa.Column("llm_provider", sa.String(20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # --------------------------------------------------------------- positions
    op.create_table(
        "positions",
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("avg_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("ticker"),
    )

    # -------------------------------------------------- portfolio_snapshots
    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("total_value", sa.Numeric(18, 6), nullable=True),
        sa.Column("cash_balance", sa.Numeric(18, 6), nullable=True),
        sa.Column(
            "snapshot_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ------------------------------------------------------------ price_cache
    op.create_table(
        "price_cache",
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("price", sa.Numeric(18, 6), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("ticker"),
    )

    # ------------------------------------------------------------ news_events
    op.create_table(
        "news_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(10), nullable=True),
        sa.Column("headline", sa.Text(), nullable=True),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column(
            "triggered",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # -------------------------------------------------------- sentiment_scores
    op.create_table(
        "sentiment_scores",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(10), nullable=True),
        sa.Column("score", sa.Numeric(6, 4), nullable=True),
        sa.Column("source", sa.String(20), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # -------------------------------------------------------- ticker_metadata
    op.create_table(
        "ticker_metadata",
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("company_name", sa.String(100), nullable=True),
        sa.Column("sector", sa.String(50), nullable=True),
        sa.Column("market_cap_tier", sa.String(10), nullable=True),
        sa.PrimaryKeyConstraint("ticker"),
    )

    # ------------------------------------------------------- skipped_triggers
    op.create_table(
        "skipped_triggers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(10), nullable=True),
        sa.Column("trigger_type", sa.String(10), nullable=True),
        sa.Column("trigger_detail", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # -------------------------------------------------- monitor_heartbeats
    op.create_table(
        "monitor_heartbeats",
        sa.Column("monitor", sa.String(20), nullable=False),
        sa.Column(
            "last_beat",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("monitor"),
    )

    # ------------------------------------------------------- market_sessions
    op.create_table(
        "market_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_date", sa.Date(), nullable=True),
        sa.Column("open_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session_type", sa.String(20), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("market_sessions")
    op.drop_table("monitor_heartbeats")
    op.drop_table("skipped_triggers")
    op.drop_table("ticker_metadata")
    op.drop_table("sentiment_scores")
    op.drop_table("news_events")
    op.drop_table("price_cache")
    op.drop_table("portfolio_snapshots")
    op.drop_table("positions")
    op.drop_table("trades")

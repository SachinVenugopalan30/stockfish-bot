"""add price_history table

Revision ID: 002
Revises: 001
Create Date: 2026-03-22 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "price_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("price", sa.Numeric(18, 6), nullable=False),
        sa.Column("volume", sa.Numeric(18, 2), nullable=True),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_price_history_ticker", "price_history", ["ticker"])
    op.create_index("ix_price_history_recorded_at", "price_history", ["recorded_at"])


def downgrade() -> None:
    op.drop_index("ix_price_history_recorded_at", "price_history")
    op.drop_index("ix_price_history_ticker", "price_history")
    op.drop_table("price_history")

"""Create technical_indicators table

Revision ID: 005
Revises: 004
Create Date: 2026-03-22
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "technical_indicators",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("indicator_type", sa.String(20), nullable=False),
        sa.Column("value", sa.Numeric(18, 6), nullable=False),
        sa.Column("signal", sa.String(20), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_technical_indicators_ticker", "technical_indicators", ["ticker"])
    op.create_index("ix_technical_indicators_computed_at", "technical_indicators", ["computed_at"])
    op.create_index(
        "ix_tech_ind_ticker_type_time",
        "technical_indicators",
        ["ticker", "indicator_type", "computed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tech_ind_ticker_type_time", table_name="technical_indicators")
    op.drop_index("ix_technical_indicators_computed_at", table_name="technical_indicators")
    op.drop_index("ix_technical_indicators_ticker", table_name="technical_indicators")
    op.drop_table("technical_indicators")

"""Add confidence and llm_model to trades

Revision ID: 004
Revises: 003
Create Date: 2026-03-22
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("llm_model", sa.String(50), nullable=True))
    op.add_column("trades", sa.Column("confidence", sa.Numeric(4, 3), nullable=True))


def downgrade() -> None:
    op.drop_column("trades", "confidence")
    op.drop_column("trades", "llm_model")

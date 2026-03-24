"""Add FinBERT columns to sentiment_scores

Revision ID: 006
Revises: 005
Create Date: 2026-03-22
"""
from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sentiment_scores", sa.Column("model", sa.String(20), nullable=True))
    op.add_column("sentiment_scores", sa.Column("raw_text", sa.Text(), nullable=True))
    op.add_column("sentiment_scores", sa.Column("positive_score", sa.Numeric(6, 4), nullable=True))
    op.add_column("sentiment_scores", sa.Column("negative_score", sa.Numeric(6, 4), nullable=True))
    op.add_column("sentiment_scores", sa.Column("neutral_score", sa.Numeric(6, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("sentiment_scores", "neutral_score")
    op.drop_column("sentiment_scores", "negative_score")
    op.drop_column("sentiment_scores", "positive_score")
    op.drop_column("sentiment_scores", "raw_text")
    op.drop_column("sentiment_scores", "model")

"""add article url to news_events and trades

Revision ID: 003
Revises: 002
Create Date: 2026-03-22 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("news_events", sa.Column("url", sa.Text(), nullable=True))
    op.add_column("trades", sa.Column("article_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("trades", "article_url")
    op.drop_column("news_events", "url")

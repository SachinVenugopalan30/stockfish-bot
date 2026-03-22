from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    total_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    cash_balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

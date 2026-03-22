from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Interval, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    action: Mapped[str] = mapped_column(String(4), nullable=False)  # buy | sell | hold
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    price_at_exec: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    hold_duration: Mapped[object | None] = mapped_column(Interval(), nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_type: Mapped[str | None] = mapped_column(String(10), nullable=True)  # price | news | reddit
    trigger_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Position(Base):
    __tablename__ = "positions"

    ticker: Mapped[str] = mapped_column(String(10), primary_key=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    avg_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

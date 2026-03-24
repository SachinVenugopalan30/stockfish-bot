from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DecisionOutcome(Base):
    __tablename__ = "decision_outcomes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(Integer, ForeignKey("trades.id", ondelete="CASCADE"), nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    action: Mapped[str] = mapped_column(String(4), nullable=False)       # buy/sell/hold
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    price_at_decision: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    signal_strength: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    price_at_1h: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    price_at_24h: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    pct_change_1h: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    pct_change_24h: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    outcome_correct_1h: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    outcome_correct_24h: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

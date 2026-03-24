from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PriceCache(Base):
    __tablename__ = "price_cache"

    ticker: Mapped[str] = mapped_column(String(10), primary_key=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NewsEvent(Base):
    __tablename__ = "news_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str | None] = mapped_column(String(10), nullable=True)
    headline: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    triggered: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SentimentScore(Base):
    __tablename__ = "sentiment_scores"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str | None] = mapped_column(String(10), nullable=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)  # -1.0 to 1.0
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # news | reddit
    model: Mapped[str | None] = mapped_column(String(20), nullable=True)    # finbert | keyword
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    positive_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    negative_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    neutral_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TickerMetadata(Base):
    __tablename__ = "ticker_metadata"

    ticker: Mapped[str] = mapped_column(String(10), primary_key=True)
    company_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(50), nullable=True)
    market_cap_tier: Mapped[str | None] = mapped_column(String(10), nullable=True)  # large | mid | small


class PriceHistory(Base):
    """Append-only time-series of every price tick — for ML and backtesting."""
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # alpaca | demo
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class TechnicalIndicator(Base):
    """Computed technical indicators — recomputed every 5 minutes by APScheduler."""
    __tablename__ = "technical_indicators"
    __table_args__ = (
        Index("ix_tech_ind_ticker_type_time", "ticker", "indicator_type", "computed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    indicator_type: Mapped[str] = mapped_column(String(20), nullable=False)  # RSI|MACD|BOLLINGER|SMA_20|EMA_12
    value: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    signal: Mapped[str] = mapped_column(String(20), nullable=False)  # oversold|overbought|neutral|bullish|bearish
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

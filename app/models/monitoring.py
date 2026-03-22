from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SkippedTrigger(Base):
    __tablename__ = "skipped_triggers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str | None] = mapped_column(String(10), nullable=True)
    trigger_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    trigger_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MonitorHeartbeat(Base):
    __tablename__ = "monitor_heartbeats"

    monitor: Mapped[str] = mapped_column(String(20), primary_key=True)  # price | news | reddit
    last_beat: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MarketSession(Base):
    __tablename__ = "market_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    open_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # regular | pre_market | after_hours

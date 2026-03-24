from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Interval, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentReasoningTrace(Base):
    """Stores the full tool-call chain for each agentic decision."""
    __tablename__ = "agent_reasoning_traces"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    tool_calls: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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
    article_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    signal_strength: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    agent_trace_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("agent_reasoning_traces.id", ondelete="SET NULL"), nullable=True
    )
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

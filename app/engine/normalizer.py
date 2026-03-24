"""
normalizer.py — Context Normalization (Step 4)

Computes normalized features in the range [-1, +1] (or [0, 1] for signal_strength)
to give the LLM a consistent signal-quality summary across all dimensions.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_data import PriceCache, PriceHistory, SentimentScore, TechnicalIndicator

if TYPE_CHECKING:
    from app.config import Settings
    from app.engine.events import TriggerEvent
    from app.engine.portfolio import PortfolioManager


@dataclass
class NormalizedFeatures:
    price_momentum: float       # -1 to +1
    technical_alignment: float  # -1 to +1
    sentiment_composite: float  # -1 to +1
    portfolio_pressure: float   # -1 to +1  (+1 = room to buy, -1 = overexposed)
    signal_strength: float      # 0 to 1

    def __post_init__(self):
        self.signal_strength = max(0.0, min(1.0, self.signal_strength))


async def compute_normalized_features(
    event: "TriggerEvent",
    session: AsyncSession,
    portfolio: "PortfolioManager",
    settings: "Settings",
    signal_strength: float,
) -> NormalizedFeatures:
    """Compute all normalized features for a given trigger event."""
    ticker = event.ticker

    price_momentum, technical_alignment, sentiment_composite, portfolio_pressure = (
        await asyncio.gather(
            _compute_price_momentum(ticker, session),
            _compute_technical_alignment(ticker, session),
            _compute_sentiment_composite(ticker, session),
            _compute_portfolio_pressure(event, session, portfolio, settings),
        )
    )

    return NormalizedFeatures(
        price_momentum=price_momentum,
        technical_alignment=technical_alignment,
        sentiment_composite=sentiment_composite,
        portfolio_pressure=portfolio_pressure,
        signal_strength=signal_strength,
    )


async def _compute_price_momentum(ticker: str, session: AsyncSession) -> float:
    """
    Compute price momentum from last 20 PriceHistory rows.
    Returns a value in [-1, +1] where ±1.0 means ≥5% move.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    result = await session.execute(
        select(PriceHistory.price)
        .where(
            PriceHistory.ticker == ticker,
            PriceHistory.recorded_at >= cutoff,
        )
        .order_by(PriceHistory.recorded_at.desc())
        .limit(20)
    )
    rows = result.scalars().all()

    if len(rows) < 2:
        return 0.0

    latest_price = float(rows[0])
    oldest_price = float(rows[-1])

    if oldest_price == 0.0:
        return 0.0

    raw = (latest_price - oldest_price) / oldest_price * 100.0
    # Clamp: 5% move = full score ±1.0
    return max(-1.0, min(1.0, raw / 5.0))


async def _compute_technical_alignment(ticker: str, session: AsyncSession) -> float:
    """
    Compute technical alignment from RSI and MACD indicators.
    Returns a value in [-1, +1].
    RSI=30 → +0.4 (oversold = bullish), RSI=70 → -0.4 (overbought = bearish).
    """
    rsi_result = await session.execute(
        select(TechnicalIndicator.value)
        .where(
            TechnicalIndicator.ticker == ticker,
            TechnicalIndicator.indicator_type == "RSI",
        )
        .order_by(TechnicalIndicator.computed_at.desc())
        .limit(1)
    )
    rsi_row = rsi_result.scalar()
    if rsi_row is not None:
        rsi_value = float(rsi_row)
        rsi_component = (50.0 - rsi_value) / 50.0
    else:
        rsi_component = 0.0

    macd_result = await session.execute(
        select(TechnicalIndicator.signal)
        .where(
            TechnicalIndicator.ticker == ticker,
            TechnicalIndicator.indicator_type == "MACD",
        )
        .order_by(TechnicalIndicator.computed_at.desc())
        .limit(1)
    )
    macd_signal = macd_result.scalar()
    if macd_signal == "bullish":
        macd_component = 1.0
    elif macd_signal == "bearish":
        macd_component = -1.0
    else:
        macd_component = 0.0

    combined = (rsi_component + macd_component) / 2.0
    return max(-1.0, min(1.0, combined))


async def _compute_sentiment_composite(ticker: str, session: AsyncSession) -> float:
    """
    Compute exponentially-weighted sentiment composite from last 10 SentimentScore rows.
    Most recent gets weight 1.0, oldest (i=9) gets weight 0.9^9 ≈ 0.387.
    Returns a value in [-1, +1].
    """
    result = await session.execute(
        select(SentimentScore.score)
        .where(SentimentScore.ticker == ticker)
        .order_by(SentimentScore.recorded_at.desc())
        .limit(10)
    )
    rows = result.scalars().all()

    if not rows:
        return 0.0

    weighted_sum = 0.0
    total_weight = 0.0
    for i, score_val in enumerate(rows):
        weight = 0.9 ** i
        weighted_sum += float(score_val) * weight
        total_weight += weight

    if total_weight <= 0:
        return 0.0

    return max(-1.0, min(1.0, weighted_sum / total_weight))


async def _compute_portfolio_pressure(
    event: "TriggerEvent",
    session: AsyncSession,
    portfolio: "PortfolioManager",
    settings: "Settings",
) -> float:
    """
    Compute portfolio pressure: +1 = lots of room to buy, -1 = overexposed.
    Formula maps [0,1] → [-1, +1] via: (wallet_pressure * 0.5 + position_pressure * 0.5) * 2.0 - 1.0
    """
    ticker = event.ticker

    # Wallet utilization
    effective_wallet = portfolio.effective_wallet
    if effective_wallet > 0:
        wallet_utilization = min(1.0, max(0.0, portfolio.invested_capital / effective_wallet))
    else:
        wallet_utilization = 0.0

    wallet_pressure = 1.0 - wallet_utilization  # 1.0 = empty wallet, 0.0 = full

    # Current position value for this ticker
    position_size = portfolio.positions.get(ticker, {}).get("quantity", 0.0)

    # Get current price from PriceCache; fallback to avg_cost
    price_result = await session.execute(
        select(PriceCache.price).where(PriceCache.ticker == ticker)
    )
    price_row = price_result.scalar()
    if price_row is not None:
        current_price = float(price_row)
    else:
        current_price = portfolio.positions.get(ticker, {}).get("avg_cost", 0.0)

    position_value = position_size * current_price

    # Portfolio value for pct calculation
    portfolio_value = await portfolio.get_portfolio_value(session)
    if portfolio_value > 0:
        position_pct = min(1.0, max(0.0, position_value / portfolio_value))
    else:
        position_pct = 0.0

    # Position pressure: how much room remains for this ticker
    max_position_pct_fraction = settings.portfolio.max_position_pct / 100.0
    if max_position_pct_fraction > 0:
        position_pressure = min(1.0, max(0.0, 1.0 - (position_pct / max_position_pct_fraction)))
    else:
        position_pressure = 1.0

    # Map [0,1] → [-1, +1]
    raw = wallet_pressure * 0.5 + position_pressure * 0.5
    return max(-1.0, min(1.0, raw * 2.0 - 1.0))

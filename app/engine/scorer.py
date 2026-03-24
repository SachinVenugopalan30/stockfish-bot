"""Signal scoring — Step 3 of the signal-quality pipeline.

Scores each TriggerEvent on a 0.0–1.0 scale where higher = stronger signal.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from statistics import mean

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.events import (
    CompositeSignal,
    NewsEvent,
    PriceSpikeEvent,
    SentimentEvent,
    TriggerEvent,
)
from app.models import PriceCache, PriceHistory, SentimentScore, TechnicalIndicator
from app.models.market_data import NewsEvent as NewsEventModel

logger = logging.getLogger(__name__)

# Source credibility lookup (case-insensitive key match)
_SOURCE_CREDIBILITY: dict[str, float] = {
    "reuters": 0.9,
    "bloomberg": 0.9,
    "cnbc": 0.8,
    "wsj": 0.85,
    "marketwatch": 0.75,
    "benzinga": 0.65,
    "seekingalpha": 0.6,
}


class SignalScorer:
    """Scores TriggerEvents to a 0.0–1.0 signal-strength value."""

    async def score(self, event: TriggerEvent, session: AsyncSession) -> float:
        """Score an event 0.0–1.0. Higher = stronger signal."""
        if isinstance(event, PriceSpikeEvent):
            return await self._score_price_spike(event, session)
        elif isinstance(event, NewsEvent):
            return await self._score_news(event, session)
        elif isinstance(event, SentimentEvent):
            return await self._score_sentiment(event, session)
        elif isinstance(event, CompositeSignal):
            return await self._score_composite(event, session)
        else:
            logger.warning("Unknown event type for scoring: %s — returning 0.0", type(event).__name__)
            return 0.0

    # ------------------------------------------------------------------
    # Price spike scoring
    # ------------------------------------------------------------------

    async def _score_price_spike(self, event: PriceSpikeEvent, session: AsyncSession) -> float:
        magnitude = min(abs(event.pct_change) / 10.0, 1.0)
        velocity = min(abs(event.pct_change) / max(event.window_min, 1), 1.0)
        direction = "bullish" if event.pct_change > 0 else "bearish"
        tech_alignment = await self._compute_tech_alignment(event.ticker, direction, session)
        sentiment_alignment = await self._get_sentiment_alignment(event.ticker, direction, session)
        return magnitude * 0.4 + velocity * 0.2 + tech_alignment * 0.3 + sentiment_alignment * 0.1

    # ------------------------------------------------------------------
    # News scoring
    # ------------------------------------------------------------------

    async def _score_news(self, event: NewsEvent, session: AsyncSession) -> float:
        source_lower = (event.source or "").lower()
        credibility = _SOURCE_CREDIBILITY.get(source_lower, 0.5)

        # Count recent NewsEvent DB rows for this ticker in the last 2 hours
        two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)
        count_result = await session.execute(
            select(func.count()).select_from(NewsEventModel).where(
                NewsEventModel.ticker == event.ticker,
                NewsEventModel.created_at >= two_hours_ago,
            )
        )
        recent_count = count_result.scalar_one()
        if recent_count <= 1:
            novelty = 1.0
        elif recent_count <= 4:
            novelty = 0.7
        else:
            novelty = 0.4

        sentiment_score = event.sentiment_score or 0.0
        strength = min(abs(sentiment_score), 1.0)
        if abs(sentiment_score) < 0.05:
            direction = "bullish"  # neutral — tech_alignment will use 0.5 either way
            tech_alignment = 0.5
        else:
            direction = "bullish" if sentiment_score > 0 else "bearish"
            tech_alignment = await self._compute_tech_alignment(event.ticker, direction, session)

        return strength * 0.4 + credibility * 0.15 + novelty * 0.15 + tech_alignment * 0.3

    # ------------------------------------------------------------------
    # Sentiment (reddit) scoring
    # ------------------------------------------------------------------

    async def _score_sentiment(self, event: SentimentEvent, session: AsyncSession) -> float:
        strength = min(abs(event.score), 1.0)
        direction = "bullish" if event.score > 0 else ("bearish" if event.score < 0 else "neutral")

        # Trend consistency: last 5 SentimentScore rows for ticker
        sent_result = await session.execute(
            select(SentimentScore)
            .where(SentimentScore.ticker == event.ticker)
            .order_by(SentimentScore.recorded_at.desc())
            .limit(5)
        )
        recent_sentiments = sent_result.scalars().all()
        if recent_sentiments:
            scores = [float(s.score) for s in recent_sentiments]
            avg = mean(scores)
            if abs(avg) < 0.1:
                trend_consistency = 0.5
            elif (avg > 0 and direction == "bullish") or (avg < 0 and direction == "bearish"):
                trend_consistency = 1.0
            else:
                trend_consistency = 0.0
        else:
            trend_consistency = 0.5

        if abs(event.score) < 0.05:
            tech_alignment = 0.5
            price_momentum = 0.5
        else:
            tech_alignment = await self._compute_tech_alignment(event.ticker, direction, session)
            price_momentum = await self._get_price_momentum(event.ticker, direction, session)

        return strength * 0.4 + trend_consistency * 0.2 + tech_alignment * 0.3 + price_momentum * 0.1

    # ------------------------------------------------------------------
    # Composite scoring
    # ------------------------------------------------------------------

    async def _score_composite(self, event: CompositeSignal, session: AsyncSession) -> float:
        if not event.events:
            base = 0.5
        else:
            component_scores = await asyncio.gather(
                *[self.score(e, session) for e in event.events]
            )
            base = mean(component_scores)

        adjustment = 0.0
        if event.agreement_score > 0.5:
            adjustment = 0.2
        elif event.agreement_score < -0.5:
            adjustment = -0.15

        return min(max(base + adjustment, 0.0), 1.0)

    # ------------------------------------------------------------------
    # Tech alignment helper
    # ------------------------------------------------------------------

    async def _compute_tech_alignment(
        self, ticker: str, direction: str, session: AsyncSession
    ) -> float:
        rsi_row = (await session.execute(
            select(TechnicalIndicator)
            .where(TechnicalIndicator.ticker == ticker,
                   TechnicalIndicator.indicator_type == "RSI")
            .order_by(TechnicalIndicator.computed_at.desc())
            .limit(1)
        )).scalar_one_or_none()

        macd_row = (await session.execute(
            select(TechnicalIndicator)
            .where(TechnicalIndicator.ticker == ticker,
                   TechnicalIndicator.indicator_type == "MACD")
            .order_by(TechnicalIndicator.computed_at.desc())
            .limit(1)
        )).scalar_one_or_none()

        if rsi_row is None:
            rsi_score = 0.5
        else:
            rsi = float(rsi_row.value)
            if direction == "bullish" and rsi < 30:
                rsi_score = 1.0
            elif direction == "bearish" and rsi > 70:
                rsi_score = 1.0
            elif direction == "bullish" and rsi > 70:
                rsi_score = 0.0
            elif direction == "bearish" and rsi < 30:
                rsi_score = 0.0
            else:
                rsi_score = 0.5

        if macd_row is None:
            macd_score = 0.25
        else:
            sig = macd_row.signal  # bullish | bearish | neutral
            if (direction == "bullish" and sig == "bullish") or (direction == "bearish" and sig == "bearish"):
                macd_score = 0.5
            elif sig in ("neutral",):
                macd_score = 0.25
            else:
                macd_score = 0.0

        return min(max((rsi_score + macd_score) / 1.5, 0.0), 1.0)

    # ------------------------------------------------------------------
    # Sentiment alignment helper (for price spike)
    # ------------------------------------------------------------------

    async def _get_sentiment_alignment(
        self, ticker: str, direction: str, session: AsyncSession
    ) -> float:
        result = await session.execute(
            select(SentimentScore)
            .where(SentimentScore.ticker == ticker)
            .order_by(SentimentScore.recorded_at.desc())
            .limit(1)
        )
        latest = result.scalar_one_or_none()
        if latest is None:
            return 0.5
        score = float(latest.score)
        if abs(score) < 0.1:
            return 0.5
        if (score > 0 and direction == "bullish") or (score < 0 and direction == "bearish"):
            return 1.0
        return 0.0

    # ------------------------------------------------------------------
    # Price momentum helper (for sentiment event)
    # ------------------------------------------------------------------

    async def _get_price_momentum(
        self, ticker: str, direction: str, session: AsyncSession
    ) -> float:
        # Current price from PriceCache
        price_result = await session.execute(
            select(PriceCache.price).where(PriceCache.ticker == ticker)
        )
        current_price = price_result.scalar_one_or_none()
        if current_price is None:
            return 0.5

        # Earliest PriceHistory entry in last 30 min
        thirty_min_ago = datetime.now(timezone.utc) - timedelta(minutes=30)
        history_result = await session.execute(
            select(PriceHistory)
            .where(
                PriceHistory.ticker == ticker,
                PriceHistory.recorded_at >= thirty_min_ago,
            )
            .order_by(PriceHistory.recorded_at.asc())
            .limit(1)
        )
        earliest = history_result.scalar_one_or_none()
        if earliest is None:
            return 0.5

        current = float(current_price)
        old = float(earliest.price)
        if old <= 0:
            return 0.5

        momentum_up = current > old
        if (momentum_up and direction == "bullish") or (not momentum_up and direction == "bearish"):
            return 1.0
        return 0.0

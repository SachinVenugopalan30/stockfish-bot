"""Tests for SignalScorer (Step 3 — Signal Scoring)."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.engine.events import CompositeSignal, NewsEvent, PriceSpikeEvent, SentimentEvent
from app.engine.scorer import SignalScorer
from app.models import Base, PriceCache, PriceHistory, SentimentScore, TechnicalIndicator

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def session(db_engine) -> AsyncSession:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        yield s


# ---------------------------------------------------------------------------
# Helpers to seed the DB
# ---------------------------------------------------------------------------


async def _add_rsi(session: AsyncSession, ticker: str, value: float, signal: str) -> None:
    session.add(
        TechnicalIndicator(
            ticker=ticker,
            indicator_type="RSI",
            value=Decimal(str(value)),
            signal=signal,
            computed_at=datetime.utcnow(),
        )
    )
    await session.flush()


async def _add_macd(session: AsyncSession, ticker: str, value: float, signal: str) -> None:
    session.add(
        TechnicalIndicator(
            ticker=ticker,
            indicator_type="MACD",
            value=Decimal(str(value)),
            signal=signal,
            computed_at=datetime.utcnow(),
        )
    )
    await session.flush()


async def _add_sentiment(session: AsyncSession, ticker: str, score: float) -> None:
    session.add(
        SentimentScore(
            ticker=ticker,
            score=Decimal(str(score)),
            recorded_at=datetime.utcnow(),
        )
    )
    await session.flush()


async def _add_price_cache(session: AsyncSession, ticker: str, price: float) -> None:
    session.add(PriceCache(ticker=ticker, price=Decimal(str(price))))
    await session.flush()


async def _add_price_history(
    session: AsyncSession, ticker: str, price: float, minutes_ago: int = 20
) -> None:
    session.add(
        PriceHistory(
            ticker=ticker,
            price=Decimal(str(price)),
            source="test",
            recorded_at=datetime.utcnow() - timedelta(minutes=minutes_ago),
        )
    )
    await session.flush()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_price_spike_strong(session: AsyncSession) -> None:
    """Large pct_change + RSI=25 (oversold) + bullish MACD → score > 0.7."""
    await _add_rsi(session, "AAPL", 25.0, "oversold")
    await _add_macd(session, "AAPL", 1.5, "bullish")

    event = PriceSpikeEvent(ticker="AAPL", pct_change=12.0, window_min=5)
    scorer = SignalScorer()
    result = await scorer.score(event, session)

    assert result > 0.7, f"Expected strong signal > 0.7, got {result}"


@pytest.mark.asyncio
async def test_score_price_spike_weak(session: AsyncSession) -> None:
    """Tiny pct_change + RSI=50 neutral + no sentiment → score < 0.5."""
    await _add_rsi(session, "MSFT", 50.0, "neutral")

    event = PriceSpikeEvent(ticker="MSFT", pct_change=0.5, window_min=5)
    scorer = SignalScorer()
    result = await scorer.score(event, session)

    assert result < 0.5, f"Expected weak signal < 0.5, got {result}"


@pytest.mark.asyncio
async def test_score_news_high_credibility(session: AsyncSession) -> None:
    """Bloomberg + strong positive sentiment + novel → score > 0.7."""
    await _add_rsi(session, "TSLA", 40.0, "neutral")
    await _add_macd(session, "TSLA", 0.5, "bullish")

    event = NewsEvent(
        ticker="TSLA",
        headline="Tesla beats estimates",
        source="bloomberg",
        sentiment_score=0.95,
    )
    scorer = SignalScorer()
    result = await scorer.score(event, session)

    assert result > 0.7, f"Expected high-credibility news score > 0.7, got {result}"


@pytest.mark.asyncio
async def test_score_news_unknown_source(session: AsyncSession) -> None:
    """Unknown source + weak sentiment + many recent news → score < 0.5."""
    from app.models.market_data import NewsEvent as NewsEventModel

    # Add 6 recent news DB rows to make novelty = 0.4
    for i in range(6):
        session.add(
            NewsEventModel(
                ticker="GME",
                headline=f"news {i}",
                created_at=datetime.utcnow() - timedelta(minutes=10),
            )
        )
    await session.flush()

    event = NewsEvent(
        ticker="GME",
        headline="GME up slightly",
        source="unknownblog",
        sentiment_score=0.1,
    )
    scorer = SignalScorer()
    result = await scorer.score(event, session)

    assert result < 0.5, f"Expected weak news score < 0.5, got {result}"


@pytest.mark.asyncio
async def test_score_sentiment_trend_consistent(session: AsyncSession) -> None:
    """Reddit score -0.8 + last 5 sentiments all negative + RSI>70 (overbought = good for bearish) → score > 0.7."""
    # Add 5 negative sentiment scores
    for _ in range(5):
        await _add_sentiment(session, "AMC", -0.75)

    # RSI > 70 with bearish direction = tech_alignment = 1.0
    await _add_rsi(session, "AMC", 75.0, "overbought")
    await _add_macd(session, "AMC", -0.3, "bearish")

    event = SentimentEvent(ticker="AMC", score=-0.8, source="reddit", post_title="AMC crashing")
    scorer = SignalScorer()
    result = await scorer.score(event, session)

    assert result > 0.7, f"Expected strong bearish sentiment score > 0.7, got {result}"


@pytest.mark.asyncio
async def test_score_composite_agreement_boost(session: AsyncSession) -> None:
    """Two bullish events → composite score gets agreement boost."""
    await _add_rsi(session, "NVDA", 45.0, "neutral")
    await _add_macd(session, "NVDA", 1.0, "bullish")

    e1 = PriceSpikeEvent(ticker="NVDA", pct_change=8.0, window_min=5)
    e2 = NewsEvent(
        ticker="NVDA",
        headline="Nvidia AI chips demand surges",
        source="bloomberg",
        sentiment_score=0.9,
    )
    composite_with_boost = CompositeSignal(
        ticker="NVDA",
        events=[e1, e2],
        dominant_direction="bullish",
        agreement_score=0.8,  # triggers +0.2 boost
    )
    composite_no_boost = CompositeSignal(
        ticker="NVDA",
        events=[e1, e2],
        dominant_direction="bullish",
        agreement_score=0.3,  # no boost
    )

    scorer = SignalScorer()
    score_with = await scorer.score(composite_with_boost, session)
    score_without = await scorer.score(composite_no_boost, session)

    assert score_with > score_without, (
        f"Expected agreement boost to raise score: {score_with} vs {score_without}"
    )
    assert score_with <= 1.0


@pytest.mark.asyncio
async def test_score_composite_mixed_no_boost(session: AsyncSession) -> None:
    """1 bullish + 1 bearish → no agreement boost, score reflects the mean."""
    await _add_rsi(session, "GOOG", 50.0, "neutral")

    e_bull = PriceSpikeEvent(ticker="GOOG", pct_change=6.0, window_min=5)
    e_bear = SentimentEvent(ticker="GOOG", score=-0.7, source="reddit", post_title="GOOG down")

    composite = CompositeSignal(
        ticker="GOOG",
        events=[e_bull, e_bear],
        dominant_direction="mixed",
        agreement_score=0.0,  # mixed signals — no boost or penalty
    )

    scorer = SignalScorer()
    # Individual scores
    bull_score = await scorer.score(e_bull, session)
    bear_score = await scorer.score(e_bear, session)
    composite_score = await scorer.score(composite, session)

    expected_base = (bull_score + bear_score) / 2.0
    assert abs(composite_score - expected_base) < 0.001, (
        f"Expected composite={expected_base:.4f}, got {composite_score:.4f}"
    )


@pytest.mark.asyncio
async def test_tech_alignment_no_indicators(session: AsyncSession) -> None:
    """When no RSI/MACD in DB → _compute_tech_alignment returns 0.5."""
    scorer = SignalScorer()
    result = await scorer._compute_tech_alignment("UNKNOWN", "bullish", session)
    # With no RSI (0.5) and no MACD (0.25): (0.5 + 0.25) / 1.5 = 0.5
    assert result == pytest.approx(0.5, abs=0.001), f"Expected 0.5, got {result}"

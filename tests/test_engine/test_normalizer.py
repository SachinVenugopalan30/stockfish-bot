"""Tests for compute_normalized_features (Step 4 — Context Normalization)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import PortfolioConfig, Settings, SignalConfig
from app.engine.events import PriceSpikeEvent
from app.engine.normalizer import NormalizedFeatures, compute_normalized_features
from app.engine.portfolio import PortfolioManager
from app.llm.base import TradeContext
from app.llm.prompt import build_user_message
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


@pytest.fixture
def default_settings() -> Settings:
    return Settings(
        portfolio=PortfolioConfig(
            starting_cash=100_000.0,
            max_position_pct=10.0,
            wallet_size=0.0,
        ),
        signal=SignalConfig(normalize_context=True),
    )


@pytest.fixture
def portfolio(default_settings) -> PortfolioManager:
    return PortfolioManager(default_settings)


def make_event(ticker: str = "AAPL") -> PriceSpikeEvent:
    return PriceSpikeEvent(ticker=ticker, pct_change=3.0, window_min=5)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_price_history(
    session: AsyncSession,
    ticker: str,
    prices: list[float],
) -> None:
    """Seed PriceHistory with prices ordered oldest→newest."""
    base_time = datetime.now(timezone.utc) - timedelta(minutes=len(prices))
    for i, price in enumerate(prices):
        session.add(
            PriceHistory(
                ticker=ticker,
                price=Decimal(str(price)),
                source="test",
                recorded_at=base_time + timedelta(minutes=i),
            )
        )
    await session.flush()


async def _seed_rsi(session: AsyncSession, ticker: str, value: float, signal: str = "neutral") -> None:
    session.add(
        TechnicalIndicator(
            ticker=ticker,
            indicator_type="RSI",
            value=Decimal(str(value)),
            signal=signal,
            computed_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()


async def _seed_macd(session: AsyncSession, ticker: str, value: float, signal: str) -> None:
    session.add(
        TechnicalIndicator(
            ticker=ticker,
            indicator_type="MACD",
            value=Decimal(str(value)),
            signal=signal,
            computed_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()


async def _seed_sentiment(session: AsyncSession, ticker: str, scores: list[float]) -> None:
    """Seed sentiment scores oldest first by timestamp (index 0 = oldest, index -1 = most recent)."""
    base_time = datetime.now(timezone.utc) - timedelta(minutes=len(scores))
    for i, score in enumerate(scores):
        session.add(
            SentimentScore(
                ticker=ticker,
                score=Decimal(str(score)),
                recorded_at=base_time + timedelta(minutes=i),
            )
        )
    await session.flush()


async def _seed_price_cache(session: AsyncSession, ticker: str, price: float) -> None:
    session.add(PriceCache(ticker=ticker, price=Decimal(str(price))))
    await session.flush()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_price_momentum_bullish(session: AsyncSession, portfolio, default_settings) -> None:
    """20 PriceHistory rows from $100 to $106 → momentum capped at +1.0 (6% > 5% threshold)."""
    # oldest price = $100, latest price = $106 (6% gain > 5% threshold)
    prices = [100.0 + i * (6.0 / 19) for i in range(20)]
    await _seed_price_history(session, "AAPL", prices)

    event = make_event("AAPL")
    nf = await compute_normalized_features(
        event=event, session=session, portfolio=portfolio, settings=default_settings, signal_strength=0.8
    )

    assert nf.price_momentum == pytest.approx(1.0, abs=0.001), (
        f"Expected +1.0 (capped), got {nf.price_momentum}"
    )


@pytest.mark.asyncio
async def test_price_momentum_bearish(session: AsyncSession, portfolio, default_settings) -> None:
    """Rows from $100 to $95 → momentum capped at -1.0 (5% loss = full negative score)."""
    prices = [100.0 - i * (5.0 / 19) for i in range(20)]
    await _seed_price_history(session, "TSLA", prices)

    event = make_event("TSLA")
    nf = await compute_normalized_features(
        event=event, session=session, portfolio=portfolio, settings=default_settings, signal_strength=0.5
    )

    assert nf.price_momentum == pytest.approx(-1.0, abs=0.001), (
        f"Expected -1.0 (capped), got {nf.price_momentum}"
    )


@pytest.mark.asyncio
async def test_price_momentum_insufficient_data(session: AsyncSession, portfolio, default_settings) -> None:
    """Only 1 PriceHistory row → price_momentum returns 0.0."""
    await _seed_price_history(session, "MSFT", [150.0])

    event = make_event("MSFT")
    nf = await compute_normalized_features(
        event=event, session=session, portfolio=portfolio, settings=default_settings, signal_strength=0.5
    )

    assert nf.price_momentum == pytest.approx(0.0, abs=0.001), (
        f"Expected 0.0 (insufficient data), got {nf.price_momentum}"
    )


@pytest.mark.asyncio
async def test_technical_alignment_bullish(session: AsyncSession, portfolio, default_settings) -> None:
    """RSI=25 (oversold) + MACD=bullish → positive technical_alignment."""
    await _seed_rsi(session, "NVDA", 25.0, "oversold")
    await _seed_macd(session, "NVDA", 1.5, "bullish")

    event = make_event("NVDA")
    nf = await compute_normalized_features(
        event=event, session=session, portfolio=portfolio, settings=default_settings, signal_strength=0.9
    )

    # rsi_component = (50 - 25) / 50 = +0.5
    # macd_component = +1.0
    # combined = (0.5 + 1.0) / 2 = 0.75 → clamped to 0.75
    assert nf.technical_alignment > 0.0, (
        f"Expected positive technical_alignment, got {nf.technical_alignment}"
    )
    assert nf.technical_alignment == pytest.approx(0.75, abs=0.001), (
        f"Expected ~0.75, got {nf.technical_alignment}"
    )


@pytest.mark.asyncio
async def test_technical_alignment_no_indicators(session: AsyncSession, portfolio, default_settings) -> None:
    """No DB rows for RSI or MACD → technical_alignment returns 0.0."""
    event = make_event("UNKNOWN_TICKER")
    nf = await compute_normalized_features(
        event=event, session=session, portfolio=portfolio, settings=default_settings, signal_strength=0.5
    )

    assert nf.technical_alignment == pytest.approx(0.0, abs=0.001), (
        f"Expected 0.0 (no indicators), got {nf.technical_alignment}"
    )


@pytest.mark.asyncio
async def test_sentiment_composite_weighted(session: AsyncSession, portfolio, default_settings) -> None:
    """10 rows: recent 5 positive (+0.8), older 5 negative (-0.8) → positive composite (recency)."""
    # Seed oldest first — they'll be ordered newest-first when queried DESC
    negative_scores = [-0.8] * 5
    positive_scores = [0.8] * 5
    # oldest first in the list (will be inserted with oldest timestamps first)
    all_scores = negative_scores + positive_scores  # negative older, positive newer
    await _seed_sentiment(session, "AMZN", all_scores)

    event = make_event("AMZN")
    nf = await compute_normalized_features(
        event=event, session=session, portfolio=portfolio, settings=default_settings, signal_strength=0.6
    )

    # Recent (positive) scores get higher weights (0.9^0 to 0.9^4)
    # Older (negative) scores get lower weights (0.9^5 to 0.9^9)
    assert nf.sentiment_composite == pytest.approx(0.206, abs=0.01), (
        f"Expected ~0.206 sentiment_composite due to recency weighting, got {nf.sentiment_composite}"
    )


@pytest.mark.asyncio
async def test_portfolio_pressure_empty_wallet(session: AsyncSession, default_settings) -> None:
    """Portfolio fully deployed → returns negative pressure (overexposed)."""
    # Create a portfolio with fully deployed capital
    pm = PortfolioManager(default_settings)
    pm.cash = 0.0  # all cash deployed
    # Add a large position to max out position_pct
    pm.positions["GOOG"] = {
        "quantity": 100.0,
        "avg_cost": 1000.0,
        "opened_at": datetime.now(timezone.utc),
    }

    await _seed_price_cache(session, "GOOG", 1000.0)

    event = make_event("GOOG")
    nf = await compute_normalized_features(
        event=event, session=session, portfolio=pm, settings=default_settings, signal_strength=0.5
    )

    # Fully deployed → should be negative (no room to buy)
    assert nf.portfolio_pressure < 0.0, (
        f"Expected negative portfolio_pressure (overexposed), got {nf.portfolio_pressure}"
    )


@pytest.mark.asyncio
async def test_normalized_features_all_zero_defaults(session: AsyncSession, portfolio, default_settings) -> None:
    """Empty DB + zero portfolio → all features close to 0.0 (except portfolio_pressure)."""
    event = make_event("EMPTY_TICKER")
    nf = await compute_normalized_features(
        event=event, session=session, portfolio=portfolio, settings=default_settings, signal_strength=0.0
    )

    assert isinstance(nf, NormalizedFeatures)
    assert nf.price_momentum == pytest.approx(0.0, abs=0.001)
    assert nf.technical_alignment == pytest.approx(0.0, abs=0.001)
    assert nf.sentiment_composite == pytest.approx(0.0, abs=0.001)
    assert nf.signal_strength == pytest.approx(0.0, abs=0.001)
    # portfolio_pressure: empty wallet (starting_cash, nothing deployed) → should be positive
    assert -1.0 <= nf.portfolio_pressure <= 1.0


@pytest.mark.asyncio
async def test_features_block_in_prompt(session: AsyncSession) -> None:
    """build_user_message with normalized_features set → output contains === SIGNAL FEATURES ===."""
    context = TradeContext(
        ticker="AAPL",
        current_price=150.0,
        trigger_type="price",
        trigger_detail="+3.0% in 5min",
        normalized_features={
            "price_momentum": 0.60,
            "technical_alignment": 0.40,
            "sentiment_composite": 0.20,
            "portfolio_pressure": 0.10,
            "signal_strength": 0.75,
        },
    )

    message = build_user_message(context)

    assert "=== SIGNAL FEATURES" in message, "Expected features block header in prompt"
    assert "Price momentum:" in message
    assert "Technical alignment:" in message
    assert "Sentiment composite:" in message
    assert "Portfolio pressure:" in message
    assert "Signal strength:" in message
    assert "positive = bullish" in message


@pytest.mark.asyncio
async def test_no_features_block_when_none(session: AsyncSession) -> None:
    """build_user_message with normalized_features=None → output does NOT contain === SIGNAL FEATURES ===."""
    context = TradeContext(
        ticker="AAPL",
        current_price=150.0,
        trigger_type="price",
        trigger_detail="+3.0% in 5min",
        normalized_features=None,
    )

    message = build_user_message(context)

    assert "=== SIGNAL FEATURES" not in message, (
        "Features block should NOT appear when normalized_features is None"
    )

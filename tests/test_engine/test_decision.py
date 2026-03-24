from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import PortfolioConfig, Settings, TriggersConfig
from app.engine.decision import DecisionEngine
from app.engine.events import PriceSpikeEvent
from app.engine.portfolio import PortfolioManager
from app.models import Base, PriceCache, SkippedTrigger, TickerMetadata, Trade
from tests.conftest import MockLLMProvider

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_db():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def settings():
    return Settings(
        triggers=TriggersConfig(cooldown_min=10, price_spike_pct=2.0),
        portfolio=PortfolioConfig(starting_cash=100000.0, max_position_pct=10.0),
    )


async def test_decision_engine_processes_event(settings, test_db):
    llm = MockLLMProvider(action="buy", quantity=5.0)
    portfolio = PortfolioManager(settings)
    engine = DecisionEngine(settings, llm, portfolio)

    # Patch async_session_factory to use test DB
    async with test_db() as session:
        # Seed price cache
        session.add(PriceCache(ticker="AAPL", price=Decimal("150.00"), updated_at=datetime.utcnow()))
        session.add(TickerMetadata(ticker="AAPL", company_name="Apple", sector="Tech", market_cap_tier="large"))
        await session.commit()

    with patch("app.engine.decision.async_session_factory", test_db):
        event = PriceSpikeEvent(ticker="AAPL", pct_change=3.0, window_min=5)
        await engine._process_event(event)

    async with test_db() as session:
        from sqlalchemy import select
        result = await session.execute(select(Trade).where(Trade.ticker == "AAPL"))
        trade = result.scalar_one()
        assert trade.action == "buy"
        assert trade.ticker == "AAPL"
        assert trade.llm_provider == "mock"


async def test_cooldown_suppresses_duplicate(settings, test_db):
    llm = MockLLMProvider(action="buy", quantity=5.0)
    portfolio = PortfolioManager(settings)
    engine = DecisionEngine(settings, llm, portfolio)

    async with test_db() as session:
        session.add(PriceCache(ticker="TSLA", price=Decimal("200.00"), updated_at=datetime.utcnow()))
        await session.commit()

    with patch("app.engine.decision.async_session_factory", test_db):
        event1 = PriceSpikeEvent(ticker="TSLA", pct_change=3.0)
        event2 = PriceSpikeEvent(ticker="TSLA", pct_change=3.5)
        await engine._process_event(event1)
        await engine._process_event(event2)  # Should be skipped (cooldown)

    async with test_db() as session:
        from sqlalchemy import func, select
        trade_count = await session.execute(
            select(func.count(Trade.id)).where(Trade.ticker == "TSLA")
        )
        skipped_count = await session.execute(
            select(func.count(SkippedTrigger.id)).where(SkippedTrigger.ticker == "TSLA")
        )
        assert trade_count.scalar() == 1
        assert skipped_count.scalar() == 1


async def test_no_price_skips_event(settings, test_db):
    llm = MockLLMProvider()
    portfolio = PortfolioManager(settings)
    engine = DecisionEngine(settings, llm, portfolio)

    with patch("app.engine.decision.async_session_factory", test_db):
        event = PriceSpikeEvent(ticker="UNKNOWN_TICKER", pct_change=5.0)
        await engine._process_event(event)  # Should not raise, just log warning

    async with test_db() as session:
        from sqlalchemy import func, select
        count = await session.execute(select(func.count(Trade.id)))
        assert count.scalar() == 0

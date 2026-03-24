
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import PortfolioConfig, Settings
from app.engine.portfolio import PortfolioManager
from app.models import Base

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def settings():
    return Settings(portfolio=PortfolioConfig(starting_cash=100000.0, max_position_pct=10.0))


async def test_portfolio_initial_state(settings):
    pm = PortfolioManager(settings)
    assert pm.cash == 100000.0
    assert pm.positions == {}


async def test_can_buy_within_limits(settings):
    pm = PortfolioManager(settings)
    # 10 shares at $500 = $5000 = 5% of $100k portfolio (under 10% limit)
    assert await pm.can_buy("NVDA", 500.0, 10.0, 100000.0) is True


async def test_can_buy_exceeds_position_limit(settings):
    pm = PortfolioManager(settings)
    # 200 shares at $500 = $100k = 100% of portfolio (over 10% limit)
    assert await pm.can_buy("NVDA", 500.0, 200.0, 100000.0) is False


async def test_can_buy_insufficient_cash(settings):
    pm = PortfolioManager(settings)
    pm.cash = 1000.0
    # $5000 cost but only $1000 cash
    assert await pm.can_buy("NVDA", 500.0, 10.0, 100000.0) is False


async def test_apply_buy(settings, session_factory):
    pm = PortfolioManager(settings)
    async with session_factory() as session:
        await pm.apply_buy("AAPL", 150.0, 10.0, session)

    assert pm.cash == 100000.0 - 150.0 * 10
    assert "AAPL" in pm.positions
    assert pm.positions["AAPL"]["quantity"] == 10.0
    assert pm.positions["AAPL"]["avg_cost"] == 150.0


async def test_apply_buy_averages_cost(settings, session_factory):
    pm = PortfolioManager(settings)
    async with session_factory() as session:
        await pm.apply_buy("AAPL", 100.0, 10.0, session)
        await pm.apply_buy("AAPL", 200.0, 10.0, session)

    assert pm.positions["AAPL"]["quantity"] == 20.0
    assert pm.positions["AAPL"]["avg_cost"] == 150.0  # avg of 100 and 200


async def test_apply_sell(settings, session_factory):
    pm = PortfolioManager(settings)
    async with session_factory() as session:
        await pm.apply_buy("AAPL", 100.0, 10.0, session)
        pnl = await pm.apply_sell("AAPL", 120.0, 5.0, session)

    assert pnl == 5.0 * (120.0 - 100.0)  # 100.0 profit
    assert pm.positions["AAPL"]["quantity"] == 5.0
    assert pm.cash == 100000.0 - 1000.0 + 600.0  # bought 1000, sold 600


async def test_apply_sell_closes_position(settings, session_factory):
    pm = PortfolioManager(settings)
    async with session_factory() as session:
        await pm.apply_buy("TSLA", 200.0, 5.0, session)
        await pm.apply_sell("TSLA", 250.0, 5.0, session)

    assert "TSLA" not in pm.positions


async def test_apply_sell_no_position(settings, session_factory):
    pm = PortfolioManager(settings)
    async with session_factory() as session:
        result = await pm.apply_sell("NVDA", 500.0, 5.0, session)
    assert result is None

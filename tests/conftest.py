import os
import pytest
import pytest_asyncio
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.models import Base
from app.config import Settings, TriggersConfig, LLMConfig, DataSourcesConfig, PortfolioConfig
from app.llm.base import LLMProvider, TradeContext, Decision
from app.engine.portfolio import PortfolioManager

# Use SQLite in-memory for tests
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return Settings(
        triggers=TriggersConfig(
            price_spike_pct=2.0,
            price_spike_window_min=5,
            cooldown_min=10,
            reddit_min_upvotes=50,
        ),
        llm=LLMConfig(provider="ollama", model="llama3"),
        data_sources=DataSourcesConfig(news_poll_interval_sec=1),
        portfolio=PortfolioConfig(starting_cash=100000.0, max_position_pct=10.0),
    )


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


class MockLLMProvider(LLMProvider):
    """Deterministic mock LLM for testing."""
    def __init__(self, action="buy", quantity=10.0, reasoning="Test decision"):
        self._action = action
        self._quantity = quantity
        self._reasoning = reasoning

    @property
    def provider_name(self) -> str:
        return "mock"

    async def decide(self, context: TradeContext) -> Decision:
        return Decision(
            action=self._action,
            quantity=self._quantity,
            reasoning=self._reasoning,
            confidence=0.8,
        )


@pytest.fixture
def mock_llm():
    return MockLLMProvider()


@pytest.fixture
def portfolio(test_settings):
    return PortfolioManager(test_settings)

import pytest
from unittest.mock import AsyncMock
from app.monitors.reddit import RedditMonitor
from app.config import Settings, TriggersConfig


@pytest.fixture
def settings():
    return Settings(triggers=TriggersConfig(reddit_min_upvotes=50))


def test_simple_sentiment_positive(settings):
    monitor = RedditMonitor(settings, AsyncMock())
    score = monitor._simple_sentiment("Bullish on NVDA, buy the dip for the moon")
    assert score > 0


def test_simple_sentiment_negative(settings):
    monitor = RedditMonitor(settings, AsyncMock())
    score = monitor._simple_sentiment("Bearish crash incoming, sell everything")
    assert score < 0


def test_simple_sentiment_neutral(settings):
    monitor = RedditMonitor(settings, AsyncMock())
    score = monitor._simple_sentiment("This is a post about stocks")
    assert score == 0.0


def test_extract_ticker_sync(settings):
    monitor = RedditMonitor(settings, AsyncMock())
    ticker_map = {"apple": "AAPL", "aapl": "AAPL"}

    result = monitor._extract_ticker_sync("Apple stock discussion", ticker_map)
    assert result == "AAPL"


def test_extract_ticker_sync_not_found(settings):
    monitor = RedditMonitor(settings, AsyncMock())
    ticker_map = {"apple": "AAPL"}

    result = monitor._extract_ticker_sync("Random text here", ticker_map)
    assert result is None


def test_reddit_monitor_name(settings):
    monitor = RedditMonitor(settings, AsyncMock())
    assert monitor.name == "reddit"

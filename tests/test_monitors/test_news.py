import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from app.monitors.news import NewsMonitor
from app.config import Settings, DataSourcesConfig


@pytest.fixture
def settings():
    return Settings(data_sources=DataSourcesConfig(news_poll_interval_sec=1))


def test_extract_ticker_found(settings):
    monitor = NewsMonitor(settings, AsyncMock())
    ticker_map = {"apple": "AAPL", "aapl": "AAPL", "nvidia": "NVDA", "nvda": "NVDA"}

    result = monitor._extract_ticker("Apple reports record sales", ticker_map)
    assert result == "AAPL"


def test_extract_ticker_not_found(settings):
    monitor = NewsMonitor(settings, AsyncMock())
    ticker_map = {"apple": "AAPL"}

    result = monitor._extract_ticker("No relevant companies here", ticker_map)
    assert result is None


def test_extract_ticker_by_symbol(settings):
    monitor = NewsMonitor(settings, AsyncMock())
    ticker_map = {"nvda": "NVDA", "nvidia corporation": "NVDA"}

    result = monitor._extract_ticker("NVDA surges after earnings", ticker_map)
    assert result == "NVDA"


def test_news_monitor_name(settings):
    monitor = NewsMonitor(settings, AsyncMock())
    assert monitor.name == "news"

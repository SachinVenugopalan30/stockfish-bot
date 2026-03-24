from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import DataSourcesConfig, Settings, TriggersConfig
from app.engine.events import PriceSpikeEvent
from app.monitors.price import PriceMonitor


@pytest.fixture
def settings():
    return Settings(
        triggers=TriggersConfig(price_spike_pct=2.0, price_spike_window_min=5),
        data_sources=DataSourcesConfig(alpaca_feed="iex"),
    )


async def test_spike_detection_positive(settings):
    received_events = []

    async def capture_event(event):
        received_events.append(event)

    monitor = PriceMonitor(settings, capture_event)

    # Mock async_session_factory used inside _handle_price_update
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    # scalar_one_or_none returns None so a new PriceCache is added
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=None)
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    with patch("app.monitors.price.async_session_factory", return_value=mock_session):
        # First update at $100 — only 1 entry so spike check returns early
        await monitor._handle_price_update("AAPL", 100.0)

        # Manually inject an old price entry far enough back to be inside window
        old_time = datetime.utcnow() - timedelta(minutes=3)
        monitor._windows["AAPL"].appendleft((old_time, 100.0))

        # Second update at $103 — 3% change exceeds 2% threshold
        await monitor._handle_price_update("AAPL", 103.0)

    # At least one spike event should have been fired
    assert len(received_events) >= 1
    assert all(isinstance(e, PriceSpikeEvent) for e in received_events)


def test_price_monitor_name(settings):
    monitor = PriceMonitor(settings, AsyncMock())
    assert monitor.name == "price"

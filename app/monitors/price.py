import asyncio
import logging
import os
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Callable, Dict, Deque, Tuple

from app.config import Settings
from app.database import async_session_factory
from app.engine.events import PriceSpikeEvent
from app.models import PriceCache, PriceHistory
from app.monitors.base import BaseMonitor
from decimal import Decimal

logger = logging.getLogger(__name__)


class PriceMonitor(BaseMonitor):
    def __init__(self, settings: Settings, event_callback: Callable):
        super().__init__("price")
        self.settings = settings
        self.event_callback = event_callback
        # ticker -> deque of (timestamp, price)
        self._windows: Dict[str, Deque[Tuple[datetime, float]]] = defaultdict(
            lambda: deque(maxlen=500)
        )

    async def _run(self) -> None:
        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")

        if not api_key or not secret_key:
            logger.warning("Alpaca API keys not set. Price monitor running in demo mode.")
            await self._run_demo_mode()
            return

        try:
            from alpaca.data.live import StockDataStream
            from alpaca.data.models import Bar

            stream = StockDataStream(api_key, secret_key, feed=self.settings.data_sources.alpaca_feed)

            async def handle_bar(bar: Bar) -> None:
                await self._handle_price_update(bar.symbol, float(bar.close))
                await self.record_heartbeat()

            # Subscribe to top tickers from ticker_metadata
            tickers = await self._get_tracked_tickers()
            if tickers:
                stream.subscribe_bars(handle_bar, *tickers)

            await stream.run()
        except Exception as e:
            logger.error(f"Price monitor error: {e}", exc_info=True)
            if self._running:
                await asyncio.sleep(5)

    async def _run_demo_mode(self) -> None:
        """Simulate price updates in demo mode."""
        import random
        tickers = await self._get_tracked_tickers()
        if not tickers:
            tickers = ["AAPL", "NVDA", "TSLA"]

        prices = {t: 100.0 + random.uniform(0, 500) for t in tickers}
        while self._running:
            for ticker in tickers:
                # Random walk
                prices[ticker] *= 1 + random.uniform(-0.005, 0.005)
                await self._handle_price_update(ticker, prices[ticker])
            await self.record_heartbeat()
            await asyncio.sleep(10)

    async def _handle_price_update(self, ticker: str, price: float) -> None:
        now = datetime.utcnow()
        self._windows[ticker].append((now, price))

        # Update price cache and append to time-series history
        async with async_session_factory() as session:
            from sqlalchemy import select
            result = await session.execute(select(PriceCache).where(PriceCache.ticker == ticker))
            cache = result.scalar_one_or_none()
            if cache:
                cache.price = Decimal(str(price))
                cache.updated_at = now
            else:
                session.add(PriceCache(ticker=ticker, price=Decimal(str(price)), updated_at=now))
            session.add(PriceHistory(
                ticker=ticker,
                price=Decimal(str(price)),
                source="alpaca" if os.environ.get("ALPACA_API_KEY") else "demo",
                recorded_at=now,
            ))
            await session.commit()

        # Check for spike
        window_min = self.settings.triggers.price_spike_window_min
        cutoff = now - timedelta(minutes=window_min)
        window = self._windows[ticker]

        # Find oldest price in window
        old_entries = [(t, p) for t, p in window if t >= cutoff]
        if len(old_entries) < 2:
            return

        oldest_price = old_entries[0][1]
        if oldest_price <= 0:
            return

        pct_change = ((price - oldest_price) / oldest_price) * 100
        if abs(pct_change) >= self.settings.triggers.price_spike_pct:
            event = PriceSpikeEvent(
                ticker=ticker,
                pct_change=pct_change,
                window_min=window_min,
            )
            await self.event_callback(event)
            logger.info(f"Price spike detected: {ticker} {pct_change:+.2f}% in {window_min}min")

    async def _get_tracked_tickers(self) -> list[str]:
        async with async_session_factory() as session:
            from sqlalchemy import select
            from app.models import TickerMetadata
            result = await session.execute(select(TickerMetadata.ticker))
            return [row[0] for row in result.fetchall()]

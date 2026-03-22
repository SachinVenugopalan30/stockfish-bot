import asyncio
import logging
from datetime import datetime
from typing import Callable, List

import feedparser

from app.config import Settings
from app.database import async_session_factory
from app.engine.events import NewsEvent
from app.models import NewsEvent as NewsEventModel
from app.monitors.base import BaseMonitor

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.marketwatch.com/marketwatch/topstories",
]


class NewsMonitor(BaseMonitor):
    def __init__(self, settings: Settings, event_callback: Callable):
        super().__init__("news")
        self.settings = settings
        self.event_callback = event_callback
        self._seen_ids: set = set()  # track ingested entry IDs

    async def _run(self) -> None:
        while self._running:
            try:
                tickers = await self._get_ticker_map()
                for feed_url in RSS_FEEDS:
                    await self._poll_feed(feed_url, tickers)
                await self.record_heartbeat()
            except Exception as e:
                logger.error(f"News monitor error: {e}", exc_info=True)
            await asyncio.sleep(self.settings.data_sources.news_poll_interval_sec)

    async def _get_ticker_map(self) -> dict:
        """Returns {company_name_lower: ticker, ticker_lower: ticker}"""
        async with async_session_factory() as session:
            from sqlalchemy import select
            from app.models import TickerMetadata
            result = await session.execute(select(TickerMetadata))
            metadata = result.scalars().all()
            mapping = {}
            for m in metadata:
                mapping[m.ticker.lower()] = m.ticker
                if m.company_name:
                    mapping[m.company_name.lower()] = m.ticker
            return mapping

    def _extract_ticker(self, text: str, ticker_map: dict) -> str | None:
        """Find a tracked ticker mentioned in text."""
        text_lower = text.lower()
        for key, ticker in ticker_map.items():
            if key in text_lower:
                return ticker
        return None

    async def _poll_feed(self, feed_url: str, ticker_map: dict) -> None:
        import urllib.parse
        source = urllib.parse.urlparse(feed_url).netloc.replace("www.", "").replace("feeds.", "")

        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            logger.warning(f"Failed to parse feed {feed_url}: {e}")
            return

        for entry in feed.entries:
            entry_id = getattr(entry, "id", entry.get("link", ""))
            if entry_id in self._seen_ids:
                continue
            self._seen_ids.add(entry_id)

            headline = getattr(entry, "title", "")
            ticker = self._extract_ticker(headline, ticker_map)
            if not ticker:
                summary = getattr(entry, "summary", "")
                ticker = self._extract_ticker(summary, ticker_map)

            async with async_session_factory() as session:
                news_record = NewsEventModel(
                    ticker=ticker or "UNKNOWN",
                    headline=headline,
                    source=source[:50],
                    triggered=bool(ticker),
                    created_at=datetime.utcnow(),
                )
                session.add(news_record)
                await session.commit()

            if ticker:
                event = NewsEvent(
                    ticker=ticker,
                    headline=headline,
                    source=source,
                )
                await self.event_callback(event)
                logger.info(f"News event: {ticker} — {headline[:80]}")

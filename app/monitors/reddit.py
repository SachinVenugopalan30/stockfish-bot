import asyncio
import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import Callable

from app.config import Settings
from app.database import async_session_factory
from app.engine.events import SentimentEvent
from app.models import SentimentScore
from app.monitors.base import BaseMonitor

logger = logging.getLogger(__name__)


class RedditMonitor(BaseMonitor):
    def __init__(self, settings: Settings, event_callback: Callable):
        super().__init__("reddit")
        self.settings = settings
        self.event_callback = event_callback

    async def _run(self) -> None:
        client_id = os.environ.get("REDDIT_CLIENT_ID", "")
        client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
        user_agent = os.environ.get("REDDIT_USER_AGENT", "stockfish-bot/1.0")

        if not client_id or not client_secret:
            logger.warning("Reddit credentials not set. Reddit monitor in demo mode.")
            await self._run_demo_mode()
            return

        try:
            import praw
            reddit = praw.Reddit(
                client_id=client_id,
                client_secret=client_secret,
                user_agent=user_agent,
                read_only=True,
            )
            subreddits = "+".join(self.settings.data_sources.reddit_subreddits)
            sub = reddit.subreddit(subreddits)

            ticker_map = await self._get_ticker_map()

            # Run blocking PRAW in executor
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._stream_posts, sub, ticker_map)
        except Exception as e:
            logger.error(f"Reddit monitor error: {e}", exc_info=True)
            if self._running:
                await asyncio.sleep(30)

    def _stream_posts(self, sub, ticker_map: dict) -> None:
        """Blocking PRAW stream — runs in executor."""
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            for submission in sub.stream.submissions(skip_existing=True):
                if not self._running:
                    break
                upvotes = getattr(submission, "score", 0)
                if upvotes < self.settings.triggers.reddit_min_upvotes:
                    continue
                title = submission.title
                ticker = self._extract_ticker_sync(title, ticker_map)
                if not ticker:
                    continue
                score = self._simple_sentiment(title)
                loop.run_until_complete(self._handle_sentiment(ticker, score, title))
                loop.run_until_complete(self.record_heartbeat())
        finally:
            loop.close()

    async def _run_demo_mode(self) -> None:
        """Demo mode: no Reddit credentials needed."""
        import random
        tickers = await self._get_tracked_tickers()
        if not tickers:
            tickers = ["AAPL", "NVDA", "TSLA"]
        while self._running:
            ticker = random.choice(tickers)
            score = random.uniform(-1, 1)
            title = f"Demo Reddit post about {ticker}"
            await self._handle_sentiment(ticker, score, title)
            await self.record_heartbeat()
            await asyncio.sleep(60)

    def _simple_sentiment(self, text: str) -> float:
        """Very basic sentiment: count positive/negative words."""
        positive = ["bullish", "buy", "moon", "surge", "beat", "profit", "gain", "up", "high", "great"]
        negative = ["bearish", "sell", "crash", "drop", "miss", "loss", "down", "low", "bad", "short"]
        text_lower = text.lower()
        pos = sum(1 for w in positive if w in text_lower)
        neg = sum(1 for w in negative if w in text_lower)
        total = pos + neg
        if total == 0:
            return 0.0
        return (pos - neg) / total

    async def _handle_sentiment(self, ticker: str, score: float, post_title: str) -> None:
        async with async_session_factory() as session:
            sentiment = SentimentScore(
                ticker=ticker,
                score=Decimal(str(round(score, 4))),
                source="reddit",
                recorded_at=datetime.utcnow(),
            )
            session.add(sentiment)
            await session.commit()

        event = SentimentEvent(
            ticker=ticker,
            score=score,
            source="reddit",
            post_title=post_title,
        )
        await self.event_callback(event)
        logger.info(f"Reddit sentiment: {ticker} score={score:.2f} — {post_title[:60]}")

    async def _get_ticker_map(self) -> dict:
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

    def _extract_ticker_sync(self, text: str, ticker_map: dict) -> str | None:
        text_lower = text.lower()
        for key, ticker in ticker_map.items():
            if key in text_lower:
                return ticker
        return None

    async def _get_tracked_tickers(self) -> list[str]:
        async with async_session_factory() as session:
            from sqlalchemy import select

            from app.models import TickerMetadata
            result = await session.execute(select(TickerMetadata.ticker))
            return [row[0] for row in result.fetchall()]

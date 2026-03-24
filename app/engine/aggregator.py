"""
SignalAggregator — collects TriggerEvents within a time window per ticker,
then flushes them as either a single pass-through or a CompositeSignal.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from app.engine.events import (
    CompositeSignal,
    NewsEvent,
    PriceSpikeEvent,
    SentimentEvent,
    TriggerEvent,
)

logger = logging.getLogger(__name__)


def _direction(event: TriggerEvent) -> float:
    """Return +1 / -1 / 0 direction for a single event."""
    if isinstance(event, PriceSpikeEvent):
        return float(_sign(event.pct_change))
    if isinstance(event, NewsEvent):
        score = event.sentiment_score
        return float(_sign(score)) if score is not None else 0.0
    if isinstance(event, SentimentEvent):
        return float(_sign(event.score))
    if isinstance(event, CompositeSignal):
        return event.agreement_score
    return 0.0


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


class SignalAggregator:
    """
    Buffers TriggerEvents per ticker for `window_sec` seconds, then flushes:
    - 1 event  → pass the original event through unchanged
    - 2+ events → build a CompositeSignal and pass that through

    `flush_callback` is an async callable (e.g. ``queue.put``).
    """

    def __init__(
        self,
        window_sec: float,
        flush_callback: Callable,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self._window_sec = window_sec
        self._flush_callback = flush_callback
        self._windows: dict[str, list[TriggerEvent]] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._pending_tasks: set[asyncio.Task] = set()
        # Accept the running event loop at construction; push() will capture it lazily as fallback.
        self._loop: Optional[asyncio.AbstractEventLoop] = loop

    async def push(self, event: TriggerEvent) -> None:
        """Accept an incoming event; start a window for its ticker if none is open."""
        if isinstance(event, CompositeSignal):
            logger.warning(
                "SignalAggregator.push() received a CompositeSignal for %s — passing through directly",
                event.ticker,
            )
            await self._flush_callback(event)
            return

        if self._loop is None:
            self._loop = asyncio.get_running_loop()

        ticker = event.ticker
        if ticker not in self._windows:
            self._windows[ticker] = [event]
            handle = self._loop.call_later(
                self._window_sec, self._flush, ticker
            )
            self._timers[ticker] = handle
            logger.debug(
                "Aggregator: opened window for %s (window=%ss)", ticker, self._window_sec
            )
        else:
            self._windows[ticker].append(event)
            logger.debug(
                "Aggregator: buffered event for %s (%d total)",
                ticker, len(self._windows[ticker]),
            )

    def _flush(self, ticker: str) -> None:
        """
        Called by the event-loop timer (synchronous).
        Builds the output event and schedules the async flush_callback.
        """
        events = self._windows.pop(ticker, [])
        self._timers.pop(ticker, None)

        if not events:
            return

        if len(events) == 1:
            output: TriggerEvent = events[0]
        else:
            directions = [_direction(e) for e in events]
            agreement_score = sum(directions) / len(directions)

            abs_score = abs(agreement_score)
            if abs_score > 0.5:
                dominant_direction = "bullish" if agreement_score > 0 else "bearish"
            else:
                dominant_direction = "mixed"

            output = CompositeSignal(
                ticker=ticker,
                events=events,
                dominant_direction=dominant_direction,
                agreement_score=agreement_score,
            )
            logger.debug(
                "Aggregator: flushing CompositeSignal for %s (%d events, %s, score=%.2f)",
                ticker, len(events), dominant_direction, agreement_score,
            )

        if self._loop is None:
            logger.error("_flush called before any push(); _loop not set — skipping flush for %s", ticker)
            return
        loop = self._loop
        task = loop.create_task(self._flush_callback(output))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

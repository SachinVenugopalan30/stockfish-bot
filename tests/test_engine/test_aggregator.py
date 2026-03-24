"""Tests for SignalAggregator (Step 2 — Event Aggregation)."""
from __future__ import annotations

import asyncio

import pytest

from app.engine.aggregator import SignalAggregator
from app.engine.events import (
    CompositeSignal,
    NewsEvent,
    PriceSpikeEvent,
    SentimentEvent,
    TriggerEvent,
)

WINDOW = 0.1  # seconds — small enough for fast tests


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _price(ticker: str, pct: float) -> PriceSpikeEvent:
    return PriceSpikeEvent(ticker=ticker, pct_change=pct)


def _news(ticker: str, sentiment: float) -> NewsEvent:
    return NewsEvent(ticker=ticker, headline="Test headline", sentiment_score=sentiment)


def _sentiment(ticker: str, score: float) -> SentimentEvent:
    return SentimentEvent(ticker=ticker, score=score, post_title="Test post")


# ---------------------------------------------------------------------------
# Test 1: single event → original type passed through (no wrapping)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_event_passthrough():
    received: list[TriggerEvent] = []

    async def callback(event: TriggerEvent) -> None:
        received.append(event)

    agg = SignalAggregator(window_sec=WINDOW, flush_callback=callback)
    event = _price("AAPL", pct=3.0)
    await agg.push(event)

    await asyncio.sleep(WINDOW + 0.05)

    assert len(received) == 1
    assert received[0] is event
    assert not isinstance(received[0], CompositeSignal)


# ---------------------------------------------------------------------------
# Test 2: two events same ticker → CompositeSignal with both
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_events_same_ticker_become_composite():
    received: list[TriggerEvent] = []

    async def callback(event: TriggerEvent) -> None:
        received.append(event)

    agg = SignalAggregator(window_sec=WINDOW, flush_callback=callback)
    e1 = _price("TSLA", pct=2.0)
    e2 = _news("TSLA", sentiment=0.8)
    await agg.push(e1)
    await agg.push(e2)

    await asyncio.sleep(WINDOW + 0.05)

    assert len(received) == 1
    composite = received[0]
    assert isinstance(composite, CompositeSignal)
    assert composite.ticker == "TSLA"
    assert len(composite.events) == 2
    assert e1 in composite.events
    assert e2 in composite.events


# ---------------------------------------------------------------------------
# Test 3: two events different tickers → two separate flush calls
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_different_tickers_two_flushes():
    received: list[TriggerEvent] = []

    async def callback(event: TriggerEvent) -> None:
        received.append(event)

    agg = SignalAggregator(window_sec=WINDOW, flush_callback=callback)
    ea = _price("AAPL", pct=1.5)
    eb = _sentiment("GOOG", score=0.6)
    await agg.push(ea)
    await agg.push(eb)

    await asyncio.sleep(WINDOW + 0.05)

    assert len(received) == 2
    tickers = {e.ticker for e in received}
    assert tickers == {"AAPL", "GOOG"}
    # Each ticker had only 1 event — should be original types
    for ev in received:
        assert not isinstance(ev, CompositeSignal)


# ---------------------------------------------------------------------------
# Test 4: agreement score — two bullish events → bullish composite
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bullish_agreement_score():
    received: list[TriggerEvent] = []

    async def callback(event: TriggerEvent) -> None:
        received.append(event)

    agg = SignalAggregator(window_sec=WINDOW, flush_callback=callback)
    await agg.push(_price("NVDA", pct=4.0))     # direction +1
    await agg.push(_news("NVDA", sentiment=0.9)) # direction +1

    await asyncio.sleep(WINDOW + 0.05)

    assert len(received) == 1
    composite = received[0]
    assert isinstance(composite, CompositeSignal)
    assert composite.dominant_direction == "bullish"
    assert composite.agreement_score > 0.5


# ---------------------------------------------------------------------------
# Test 5: mixed signals → dominant_direction == "mixed"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mixed_signals():
    received: list[TriggerEvent] = []

    async def callback(event: TriggerEvent) -> None:
        received.append(event)

    agg = SignalAggregator(window_sec=WINDOW, flush_callback=callback)
    await agg.push(_price("MSFT", pct=3.0))      # direction +1
    await agg.push(_news("MSFT", sentiment=-0.8)) # direction -1

    await asyncio.sleep(WINDOW + 0.05)

    assert len(received) == 1
    composite = received[0]
    assert isinstance(composite, CompositeSignal)
    assert composite.dominant_direction == "mixed"
    # agreement_score should be 0 (average of +1 and -1)
    assert composite.agreement_score == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Test 6: no double-flush — timer fires once; new event after flush starts fresh window
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_double_flush_new_window_after_flush():
    received: list[TriggerEvent] = []

    async def callback(event: TriggerEvent) -> None:
        received.append(event)

    agg = SignalAggregator(window_sec=WINDOW, flush_callback=callback)

    # First event → opens window
    e1 = _price("AMD", pct=2.0)
    await agg.push(e1)

    # Wait for window to expire and flush
    await asyncio.sleep(WINDOW + 0.05)
    assert len(received) == 1
    assert received[0] is e1

    # Push a second event after flush → should open a NEW window
    e2 = _sentiment("AMD", score=0.5)
    await agg.push(e2)

    await asyncio.sleep(WINDOW + 0.05)

    # Total should be 2 flushes, each with a single original event
    assert len(received) == 2
    assert received[1] is e2
    assert not isinstance(received[1], CompositeSignal)

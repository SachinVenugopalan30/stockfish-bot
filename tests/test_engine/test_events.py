from datetime import datetime

from app.engine.events import NewsEvent, PriceSpikeEvent, SentimentEvent


def test_price_spike_event():
    event = PriceSpikeEvent(ticker="AAPL", pct_change=3.5, window_min=5)
    assert event.ticker == "AAPL"
    assert event.trigger_type == "price"
    assert "+3.5%" in event.trigger_detail
    assert "5min" in event.trigger_detail


def test_price_spike_negative():
    event = PriceSpikeEvent(ticker="TSLA", pct_change=-2.8, window_min=5)
    assert "-2.8%" in event.trigger_detail


def test_news_event():
    event = NewsEvent(ticker="NVDA", headline="Nvidia reports record revenue", source="reuters")
    assert event.ticker == "NVDA"
    assert event.trigger_type == "news"
    assert event.trigger_detail == "Nvidia reports record revenue"


def test_sentiment_event():
    event = SentimentEvent(ticker="GME", score=0.9, post_title="GME to the moon!")
    assert event.ticker == "GME"
    assert event.trigger_type == "reddit"
    assert event.trigger_detail == "GME to the moon!"


def test_event_has_created_at():
    event = PriceSpikeEvent(ticker="AAPL", pct_change=2.0)
    assert isinstance(event.created_at, datetime)

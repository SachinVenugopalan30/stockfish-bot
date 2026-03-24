from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import NewsEvent, PriceCache, PriceHistory, SentimentScore, TickerMetadata


async def test_price_cache(db_session):
    cache = PriceCache(ticker="AAPL", price=Decimal("150.25"), updated_at=datetime.utcnow())
    db_session.add(cache)
    await db_session.commit()

    result = await db_session.execute(select(PriceCache).where(PriceCache.ticker == "AAPL"))
    saved = result.scalar_one()
    assert float(saved.price) == 150.25


async def test_news_event(db_session):
    event = NewsEvent(
        ticker="TSLA",
        headline="Tesla beats Q3 earnings estimates",
        source="reuters",
        triggered=True,
        created_at=datetime.utcnow(),
    )
    db_session.add(event)
    await db_session.commit()

    result = await db_session.execute(select(NewsEvent).where(NewsEvent.ticker == "TSLA"))
    saved = result.scalar_one()
    assert saved.triggered is True
    assert "Tesla" in saved.headline


async def test_sentiment_score(db_session):
    score = SentimentScore(
        ticker="NVDA",
        score=Decimal("0.75"),
        source="reddit",
        recorded_at=datetime.utcnow(),
    )
    db_session.add(score)
    await db_session.commit()

    result = await db_session.execute(select(SentimentScore).where(SentimentScore.ticker == "NVDA"))
    saved = result.scalar_one()
    assert float(saved.score) == 0.75
    assert saved.source == "reddit"


async def test_price_history_appends_ticks(db_session):
    ticks = [
        PriceHistory(ticker="AAPL", price=Decimal(str(150 + i)), source="demo", recorded_at=datetime.utcnow())
        for i in range(5)
    ]
    db_session.add_all(ticks)
    await db_session.commit()

    result = await db_session.execute(select(PriceHistory).where(PriceHistory.ticker == "AAPL"))
    rows = result.scalars().all()
    assert len(rows) == 5
    assert all(r.source == "demo" for r in rows)


async def test_price_history_volume_nullable(db_session):
    tick = PriceHistory(ticker="NVDA", price=Decimal("500.00"), source="alpaca", recorded_at=datetime.utcnow())
    db_session.add(tick)
    await db_session.commit()

    result = await db_session.execute(select(PriceHistory).where(PriceHistory.ticker == "NVDA"))
    saved = result.scalar_one()
    assert saved.volume is None
    assert float(saved.price) == 500.0


async def test_ticker_metadata(db_session):
    meta = TickerMetadata(
        ticker="MSFT",
        company_name="Microsoft Corporation",
        sector="Technology",
        market_cap_tier="large",
    )
    db_session.add(meta)
    await db_session.commit()

    result = await db_session.execute(select(TickerMetadata).where(TickerMetadata.ticker == "MSFT"))
    saved = result.scalar_one()
    assert saved.company_name == "Microsoft Corporation"
    assert saved.sector == "Technology"

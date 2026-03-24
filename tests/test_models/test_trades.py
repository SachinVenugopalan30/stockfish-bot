from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import Position, Trade


async def test_create_trade(db_session):
    trade = Trade(
        ticker="AAPL",
        action="buy",
        quantity=Decimal("10"),
        price_at_exec=Decimal("150.00"),
        entry_price=Decimal("150.00"),
        reasoning="Strong momentum signal",
        trigger_type="price",
        trigger_detail="+2.5% in 5min",
        llm_provider="mock",
        created_at=datetime.utcnow(),
    )
    db_session.add(trade)
    await db_session.commit()

    result = await db_session.execute(select(Trade).where(Trade.ticker == "AAPL"))
    saved = result.scalar_one()
    assert saved.ticker == "AAPL"
    assert saved.action == "buy"
    assert float(saved.quantity) == 10.0


async def test_trade_nullable_fields(db_session):
    trade = Trade(
        ticker="TSLA",
        action="hold",
        quantity=Decimal("0"),
        price_at_exec=Decimal("200.00"),
        reasoning="Insufficient signal",
        trigger_type="news",
        trigger_detail="Some headline",
        llm_provider="mock",
        created_at=datetime.utcnow(),
    )
    db_session.add(trade)
    await db_session.commit()

    result = await db_session.execute(select(Trade).where(Trade.ticker == "TSLA"))
    saved = result.scalar_one()
    assert saved.exit_price is None
    assert saved.realized_pnl is None
    assert saved.hold_duration is None


async def test_create_position(db_session):
    pos = Position(
        ticker="NVDA",
        quantity=Decimal("5"),
        avg_cost=Decimal("500.00"),
        opened_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(pos)
    await db_session.commit()

    result = await db_session.execute(select(Position).where(Position.ticker == "NVDA"))
    saved = result.scalar_one()
    assert saved.ticker == "NVDA"
    assert float(saved.quantity) == 5.0

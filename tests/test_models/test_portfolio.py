import pytest
from decimal import Decimal
from datetime import datetime
from sqlalchemy import select
from app.models import PortfolioSnapshot


async def test_create_snapshot(db_session):
    snapshot = PortfolioSnapshot(
        total_value=Decimal("105000.00"),
        cash_balance=Decimal("90000.00"),
        snapshot_at=datetime.utcnow(),
    )
    db_session.add(snapshot)
    await db_session.commit()

    result = await db_session.execute(select(PortfolioSnapshot))
    saved = result.scalar_one()
    assert float(saved.total_value) == 105000.0
    assert float(saved.cash_balance) == 90000.0


async def test_multiple_snapshots(db_session):
    snapshots = [
        PortfolioSnapshot(
            total_value=Decimal(str(100000 + i * 1000)),
            cash_balance=Decimal("90000"),
            snapshot_at=datetime.utcnow(),
        )
        for i in range(5)
    ]
    db_session.add_all(snapshots)
    await db_session.commit()

    result = await db_session.execute(select(PortfolioSnapshot))
    all_snapshots = result.scalars().all()
    assert len(all_snapshots) == 5

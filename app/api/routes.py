import logging
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.websocket import manager
from app.database import get_db
from app.models import MonitorHeartbeat, PortfolioSnapshot, PriceCache, SkippedTrigger, Trade
from app.models import NewsEvent as NewsEventModel
from app.schemas.responses import (
    MonitorStatus,
    NewsSignalResponse,
    PortfolioResponse,
    PositionResponse,
    SkippedSignalResponse,
    SnapshotResponse,
    StatsResponse,
    StatusResponse,
    TradeResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# State injected by main.py at startup
_bot_state: dict = {
    "running": False,
    "llm_provider": "unknown",
    "llm_model": "unknown",
    "last_decision_at": None,
    "start_time": datetime.utcnow(),
    "portfolio": None,
}


def set_bot_state(state: dict) -> None:
    _bot_state.update(state)


@router.get("/status", response_model=StatusResponse)
async def get_status(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MonitorHeartbeat))
    heartbeats = result.scalars().all()

    monitors = []
    for hb in heartbeats:
        age = (datetime.utcnow() - hb.last_beat.replace(tzinfo=None)).total_seconds()
        status = "ok" if age < 120 else "stale"
        monitors.append(MonitorStatus(name=hb.monitor, last_beat=hb.last_beat, status=status))

    uptime = (datetime.utcnow() - _bot_state["start_time"]).total_seconds()
    return StatusResponse(
        bot_running=_bot_state["running"],
        llm_provider=_bot_state["llm_provider"],
        llm_model=_bot_state["llm_model"],
        last_decision_at=_bot_state.get("last_decision_at"),
        monitors=monitors,
        uptime_seconds=uptime,
    )


@router.get("/portfolio", response_model=PortfolioResponse)
async def get_portfolio(db: AsyncSession = Depends(get_db)):
    portfolio = _bot_state.get("portfolio")
    if not portfolio:
        return PortfolioResponse(total_value=0, cash_balance=0)

    positions_out = []
    total_unrealized = 0.0

    for ticker, pos in portfolio.positions.items():
        price_result = await db.execute(
            select(PriceCache.price).where(PriceCache.ticker == ticker)
        )
        price = float(price_result.scalar() or pos["avg_cost"])
        current_value = pos["quantity"] * price
        unrealized = pos["quantity"] * (price - pos["avg_cost"])
        total_unrealized += unrealized
        positions_out.append(PositionResponse(
            ticker=ticker,
            quantity=pos["quantity"],
            avg_cost=pos["avg_cost"],
            current_value=current_value,
            unrealized_pnl=unrealized,
        ))

    total_value = await portfolio.get_portfolio_value(db)
    return PortfolioResponse(
        total_value=total_value,
        cash_balance=portfolio.cash,
        positions=positions_out,
        total_unrealized_pnl=total_unrealized,
    )


@router.get("/portfolio/snapshots", response_model=List[SnapshotResponse])
async def get_snapshots(
    days: int = Query(90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.snapshot_at >= cutoff)
        .order_by(PortfolioSnapshot.snapshot_at.asc())
    )
    snapshots = result.scalars().all()
    return [
        SnapshotResponse(
            id=s.id,
            total_value=float(s.total_value),
            cash_balance=float(s.cash_balance),
            snapshot_at=s.snapshot_at,
        )
        for s in snapshots
    ]


@router.get("/trades", response_model=List[TradeResponse])
async def get_trades(
    limit: int = Query(50, ge=1, le=500),
    days: int = Query(90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(Trade)
        .where(Trade.created_at >= cutoff)
        .order_by(Trade.created_at.desc())
        .limit(limit)
    )
    trades = result.scalars().all()
    return [
        TradeResponse(
            id=t.id,
            ticker=t.ticker,
            action=t.action,
            quantity=float(t.quantity or 0),
            price_at_exec=float(t.price_at_exec or 0),
            entry_price=float(t.entry_price) if t.entry_price else None,
            exit_price=float(t.exit_price) if t.exit_price else None,
            realized_pnl=float(t.realized_pnl) if t.realized_pnl else None,
            reasoning=t.reasoning or "",
            trigger_type=t.trigger_type or "",
            trigger_detail=t.trigger_detail or "",
            llm_provider=t.llm_provider or "",
            created_at=t.created_at,
        )
        for t in trades
    ]


@router.get("/stats", response_model=StatsResponse)
async def get_stats(db: AsyncSession = Depends(get_db)):
    total = await db.execute(select(func.count(Trade.id)))
    buys = await db.execute(select(func.count(Trade.id)).where(Trade.action == "buy"))
    sells = await db.execute(select(func.count(Trade.id)).where(Trade.action == "sell"))
    holds = await db.execute(select(func.count(Trade.id)).where(Trade.action == "hold"))

    # Win rate: sells with positive PnL
    winning_sells = await db.execute(
        select(func.count(Trade.id))
        .where(and_(Trade.action == "sell", Trade.realized_pnl > 0))
    )
    sell_count = sells.scalar() or 0
    win_rate = (winning_sells.scalar() or 0) / sell_count if sell_count > 0 else 0.0

    total_pnl = await db.execute(select(func.sum(Trade.realized_pnl)))

    portfolio = _bot_state.get("portfolio")
    total_unrealized = 0.0
    if portfolio:
        async with portfolio._lock:
            for pos in portfolio.positions.values():
                total_unrealized += pos.get("quantity", 0) * 0  # simplified

    return StatsResponse(
        total_trades=total.scalar() or 0,
        buy_count=buys.scalar() or 0,
        sell_count=sell_count,
        hold_count=holds.scalar() or 0,
        win_rate=win_rate,
        total_realized_pnl=float(total_pnl.scalar() or 0),
        total_unrealized_pnl=total_unrealized,
    )


@router.get("/signals/news", response_model=List[NewsSignalResponse])
async def get_news_signals(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(NewsEventModel)
        .order_by(NewsEventModel.created_at.desc())
        .limit(limit)
    )
    events = result.scalars().all()
    return [
        NewsSignalResponse(
            id=e.id,
            ticker=e.ticker,
            headline=e.headline,
            source=e.source,
            triggered=e.triggered,
            created_at=e.created_at,
        )
        for e in events
    ]


@router.get("/signals/skipped", response_model=List[SkippedSignalResponse])
async def get_skipped_signals(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SkippedTrigger)
        .order_by(SkippedTrigger.created_at.desc())
        .limit(limit)
    )
    skipped = result.scalars().all()
    return [
        SkippedSignalResponse(
            id=s.id,
            ticker=s.ticker,
            trigger_type=s.trigger_type,
            trigger_detail=s.trigger_detail or "",
            reason=s.reason or "",
            created_at=s.created_at,
        )
        for s in skipped
    ]


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, wait for client pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

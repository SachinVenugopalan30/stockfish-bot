from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class MonitorStatus(BaseModel):
    name: str
    last_beat: Optional[datetime] = None
    status: str  # "ok" | "stale" | "unknown"


class StatusResponse(BaseModel):
    bot_running: bool
    llm_provider: str
    llm_model: str
    last_decision_at: Optional[datetime] = None
    monitors: List[MonitorStatus] = []
    uptime_seconds: float = 0.0


class PositionResponse(BaseModel):
    ticker: str
    quantity: float
    avg_cost: float
    current_value: float
    unrealized_pnl: float


class PortfolioResponse(BaseModel):
    total_value: float
    cash_balance: float
    positions: List[PositionResponse] = []
    total_unrealized_pnl: float = 0.0


class SnapshotResponse(BaseModel):
    id: int
    total_value: float
    cash_balance: float
    snapshot_at: datetime

    model_config = {"from_attributes": True}


class TradeResponse(BaseModel):
    id: int
    ticker: str
    action: str
    quantity: float
    price_at_exec: float
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    realized_pnl: Optional[float] = None
    reasoning: str
    trigger_type: str
    trigger_detail: str
    llm_provider: str
    created_at: datetime

    model_config = {"from_attributes": True}


class StatsResponse(BaseModel):
    total_trades: int
    buy_count: int
    sell_count: int
    hold_count: int
    win_rate: float  # % of sells with positive PnL
    total_realized_pnl: float
    total_unrealized_pnl: float


class NewsSignalResponse(BaseModel):
    id: int
    ticker: str
    headline: str
    source: str
    triggered: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class SkippedSignalResponse(BaseModel):
    id: int
    ticker: str
    trigger_type: str
    trigger_detail: str
    reason: str
    created_at: datetime

    model_config = {"from_attributes": True}

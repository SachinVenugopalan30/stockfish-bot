import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Position, PriceCache, Trade


class PortfolioManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cash: float = settings.portfolio.starting_cash
        self.positions: Dict[str, dict] = {}  # ticker -> {quantity, avg_cost, opened_at}
        self._lock = asyncio.Lock()

    @property
    def effective_wallet(self) -> float:
        """Max capital the bot may deploy. 0 in config → use starting_cash."""
        ws = self.settings.portfolio.wallet_size
        return ws if ws > 0 else self.settings.portfolio.starting_cash

    @property
    def invested_capital(self) -> float:
        """Total capital currently deployed in open positions (at avg cost)."""
        return sum(
            p["quantity"] * p["avg_cost"] for p in self.positions.values()
        )

    async def load_from_db(self, session: AsyncSession) -> None:
        """Load current state from DB on startup."""
        # Load positions
        result = await session.execute(select(Position))
        positions = result.scalars().all()
        for pos in positions:
            self.positions[pos.ticker] = {
                "quantity": float(pos.quantity),
                "avg_cost": float(pos.avg_cost),
                "opened_at": pos.opened_at,
            }

        # Calculate cash: starting_cash minus all buy costs plus all sell proceeds
        buy_result = await session.execute(
            select(func.sum(Trade.price_at_exec * Trade.quantity))
            .where(Trade.action == "buy")
        )
        sell_result = await session.execute(
            select(func.sum(Trade.price_at_exec * Trade.quantity))
            .where(Trade.action == "sell")
        )
        total_bought = float(buy_result.scalar() or 0)
        total_sold = float(sell_result.scalar() or 0)
        self.cash = self.settings.portfolio.starting_cash - total_bought + total_sold

    async def get_portfolio_value(self, session: AsyncSession) -> float:
        """Calculate total portfolio value: cash + sum(position * current_price)."""
        value = self.cash
        for ticker, pos in self.positions.items():
            result = await session.execute(
                select(PriceCache.price).where(PriceCache.ticker == ticker)
            )
            price = result.scalar()
            if price:
                value += pos["quantity"] * float(price)
            else:
                value += pos["quantity"] * pos["avg_cost"]
        return value

    async def can_buy(self, ticker: str, price: float, quantity: float, portfolio_value: float) -> bool:
        """Check if buy respects max_position_pct, cash limits, and wallet cap."""
        cost = price * quantity
        if cost > self.cash:
            return False
        # Wallet cap: don't deploy beyond effective_wallet
        if self.invested_capital + cost > self.effective_wallet:
            return False
        max_value = portfolio_value * (self.settings.portfolio.max_position_pct / 100)
        current_value = self.positions.get(ticker, {}).get("quantity", 0) * price
        return (current_value + cost) <= max_value

    async def apply_buy(self, ticker: str, price: float, quantity: float, session: AsyncSession) -> None:
        async with self._lock:
            cost = price * quantity
            self.cash -= cost
            if ticker in self.positions:
                pos = self.positions[ticker]
                total_qty = pos["quantity"] + quantity
                total_cost = pos["quantity"] * pos["avg_cost"] + cost
                pos["quantity"] = total_qty
                pos["avg_cost"] = total_cost / total_qty
            else:
                self.positions[ticker] = {
                    "quantity": quantity,
                    "avg_cost": price,
                    "opened_at": datetime.utcnow(),
                }
            # Upsert position in DB
            result = await session.execute(select(Position).where(Position.ticker == ticker))
            db_pos = result.scalar_one_or_none()
            if db_pos:
                db_pos.quantity = Decimal(str(self.positions[ticker]["quantity"]))
                db_pos.avg_cost = Decimal(str(self.positions[ticker]["avg_cost"]))
                db_pos.updated_at = datetime.utcnow()
            else:
                db_pos = Position(
                    ticker=ticker,
                    quantity=Decimal(str(quantity)),
                    avg_cost=Decimal(str(price)),
                    opened_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                session.add(db_pos)
            await session.flush()

    async def apply_sell(self, ticker: str, price: float, quantity: float, session: AsyncSession) -> Optional[float]:
        """Returns realized PnL or None if no position."""
        async with self._lock:
            if ticker not in self.positions:
                return None
            pos = self.positions[ticker]
            sell_qty = min(quantity, pos["quantity"])
            realized_pnl = sell_qty * (price - pos["avg_cost"])
            self.cash += sell_qty * price
            pos["quantity"] -= sell_qty
            if pos["quantity"] <= 0:
                del self.positions[ticker]
                await session.execute(
                    Position.__table__.delete().where(Position.ticker == ticker)
                )
            else:
                result = await session.execute(select(Position).where(Position.ticker == ticker))
                db_pos = result.scalar_one_or_none()
                if db_pos:
                    db_pos.quantity = Decimal(str(pos["quantity"]))
                    db_pos.updated_at = datetime.utcnow()
                await session.flush()
            return realized_pnl

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import Settings
from app.database import async_session_factory
from app.engine.events import TriggerEvent
from app.engine.portfolio import PortfolioManager
from app.llm.base import LLMProvider, TradeContext
from app.models import Trade, SkippedTrigger, SentimentScore, PortfolioSnapshot, PriceCache

logger = logging.getLogger(__name__)


class DecisionEngine:
    def __init__(self, settings: Settings, llm: LLMProvider, portfolio: PortfolioManager):
        self.settings = settings
        self.llm = llm
        self.portfolio = portfolio
        self.queue: asyncio.Queue[TriggerEvent] = asyncio.Queue()
        self._cooldowns: Dict[str, datetime] = {}
        self._broadcast_callback: Optional[Callable] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def set_broadcast_callback(self, callback: Callable) -> None:
        self._broadcast_callback = callback

    async def push_event(self, event: TriggerEvent) -> None:
        await self.queue.put(event)

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("DecisionEngine started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("DecisionEngine stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                await self._process_event(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing event: {e}", exc_info=True)

    def _is_in_cooldown(self, ticker: str) -> Optional[str]:
        last = self._cooldowns.get(ticker)
        if last is None:
            return None
        elapsed = (datetime.utcnow() - last).total_seconds() / 60
        if elapsed < self.settings.triggers.cooldown_min:
            return f"cooldown: last trade {elapsed:.1f}min ago"
        return None

    async def _process_event(self, event: TriggerEvent) -> None:
        async with async_session_factory() as session:
            cooldown_reason = self._is_in_cooldown(event.ticker)
            if cooldown_reason:
                skipped = SkippedTrigger(
                    ticker=event.ticker,
                    trigger_type=event.trigger_type,
                    trigger_detail=event.trigger_detail,
                    reason=cooldown_reason,
                    created_at=datetime.utcnow(),
                )
                session.add(skipped)
                await session.commit()
                logger.debug(f"Skipped {event.ticker}: {cooldown_reason}")
                return

            # Get current price
            price_result = await session.execute(
                select(PriceCache.price).where(PriceCache.ticker == event.ticker)
            )
            current_price = float(price_result.scalar() or 0)
            if current_price <= 0:
                logger.warning(f"No price for {event.ticker}, skipping")
                return

            # Get recent sentiment
            sentiment_result = await session.execute(
                select(SentimentScore.score)
                .where(SentimentScore.ticker == event.ticker)
                .order_by(SentimentScore.recorded_at.desc())
                .limit(1)
            )
            recent_sentiment = float(sentiment_result.scalar() or 0.0)

            portfolio_value = await self.portfolio.get_portfolio_value(session)
            pos = self.portfolio.positions.get(event.ticker, {})

            context = TradeContext(
                ticker=event.ticker,
                current_price=current_price,
                trigger_type=event.trigger_type,
                trigger_detail=event.trigger_detail,
                position_quantity=pos.get("quantity", 0.0),
                position_avg_cost=pos.get("avg_cost", 0.0),
                cash_balance=self.portfolio.cash,
                max_position_pct=self.settings.portfolio.max_position_pct,
                portfolio_value=portfolio_value,
                recent_sentiment=recent_sentiment,
            )

            decision = await self.llm.decide(context)
            logger.info(f"Decision for {event.ticker}: {decision.action} x{decision.quantity} — {decision.reasoning}")

            # Apply portfolio changes
            realized_pnl = None
            if decision.action == "buy" and decision.quantity > 0:
                if await self.portfolio.can_buy(event.ticker, current_price, decision.quantity, portfolio_value):
                    await self.portfolio.apply_buy(event.ticker, current_price, decision.quantity, session)
            elif decision.action == "sell" and decision.quantity > 0:
                realized_pnl = await self.portfolio.apply_sell(event.ticker, current_price, decision.quantity, session)

            # Write trade record
            trade = Trade(
                ticker=event.ticker,
                action=decision.action,
                quantity=Decimal(str(decision.quantity)),
                price_at_exec=Decimal(str(current_price)),
                entry_price=Decimal(str(pos.get("avg_cost", current_price))),
                exit_price=Decimal(str(current_price)) if decision.action == "sell" else None,
                realized_pnl=Decimal(str(realized_pnl)) if realized_pnl is not None else None,
                reasoning=decision.reasoning,
                trigger_type=event.trigger_type,
                trigger_detail=event.trigger_detail,
                llm_provider=self.llm.provider_name,
                created_at=datetime.utcnow(),
            )
            session.add(trade)
            await session.commit()

            # Update cooldown
            self._cooldowns[event.ticker] = datetime.utcnow()

            # Broadcast via WebSocket
            if self._broadcast_callback:
                message = {
                    "type": "trade",
                    "ticker": event.ticker,
                    "action": decision.action,
                    "price": current_price,
                    "quantity": decision.quantity,
                    "reasoning": decision.reasoning,
                    "trigger_type": event.trigger_type,
                    "trigger_detail": event.trigger_detail,
                    "llm_provider": self.llm.provider_name,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }
                await self._broadcast_callback(message)

    async def take_portfolio_snapshot(self) -> None:
        """Called hourly by APScheduler."""
        async with async_session_factory() as session:
            portfolio_value = await self.portfolio.get_portfolio_value(session)
            snapshot = PortfolioSnapshot(
                total_value=Decimal(str(portfolio_value)),
                cash_balance=Decimal(str(self.portfolio.cash)),
                snapshot_at=datetime.utcnow(),
            )
            session.add(snapshot)
            await session.commit()
            logger.info(f"Portfolio snapshot: ${portfolio_value:.2f}")

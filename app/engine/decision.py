import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Callable, Dict, Optional

if TYPE_CHECKING:
    from app.engine.calibration import CalibrationTracker
    from app.engine.scorer import SignalScorer

from sqlalchemy import select

from app.config import Settings
from app.database import async_session_factory
from app.engine.aggregator import SignalAggregator
from app.engine.events import TriggerEvent
from app.engine.portfolio import PortfolioManager
from app.llm.base import Decision, HeldPosition, LLMProvider, TradeContext
from app.models import AgentReasoningTrace, PortfolioSnapshot, PriceCache, SentimentScore, SkippedTrigger, Trade

# Lazy import to avoid circular dependency — routes sets this reference
_update_last_decision: Optional[Callable] = None


def set_last_decision_callback(fn: Callable) -> None:
    global _update_last_decision
    _update_last_decision = fn

logger = logging.getLogger(__name__)


class DecisionEngine:
    def __init__(self, settings: Settings, llm: LLMProvider, portfolio: PortfolioManager, ta_service=None):
        self.settings = settings
        self.llm = llm
        self.portfolio = portfolio
        self.ta_service = ta_service
        self.queue: asyncio.Queue[TriggerEvent] = asyncio.Queue()
        self._cooldowns: Dict[str, datetime] = {}
        self._broadcast_callback: Optional[Callable] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._aggregator: Optional[SignalAggregator] = None
        self._scorer: Optional["SignalScorer"] = None
        self._calibration_tracker: Optional["CalibrationTracker"] = None

    def set_broadcast_callback(self, callback: Callable) -> None:
        self._broadcast_callback = callback

    def set_calibration_tracker(self, tracker) -> None:
        self._calibration_tracker = tracker

    async def push_event(self, event: TriggerEvent) -> None:
        if self._aggregator is not None:
            await self._aggregator.push(event)
        else:
            await self.queue.put(event)

    async def start(self) -> None:
        self._running = True
        if self.settings.signal.aggregation_enabled:
            self._aggregator = SignalAggregator(
                window_sec=self.settings.signal.aggregation_window_sec,
                flush_callback=self.queue.put,
                loop=asyncio.get_running_loop(),
            )
        if self.settings.signal.scoring_enabled:
            from app.engine.scorer import SignalScorer
            self._scorer = SignalScorer()
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
        elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 60
        cooldown = (
            self.settings.signal.post_trade_cooldown_min
            if self._aggregator is not None
            else self.settings.triggers.cooldown_min
        )
        if elapsed < cooldown:
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
                    created_at=datetime.now(timezone.utc),
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

            # Signal scoring
            signal_strength: Optional[float] = None
            if self.settings.signal.scoring_enabled:
                signal_strength = await self._scorer.score(event, session)
                if signal_strength < self.settings.signal.min_signal_strength:
                    skipped = SkippedTrigger(
                        ticker=event.ticker,
                        trigger_type=event.trigger_type,
                        trigger_detail=event.trigger_detail,
                        reason=f"weak signal: strength={signal_strength:.3f} < threshold={self.settings.signal.min_signal_strength:.3f}",
                        created_at=datetime.now(timezone.utc),
                    )
                    session.add(skipped)
                    await session.commit()
                    logger.debug(f"Skipped {event.ticker}: weak signal strength {signal_strength:.3f}")
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

            # Early exit: wallet at capacity AND no position in this ticker to sell/hold.
            if (self.portfolio.invested_capital >= self.portfolio.effective_wallet
                    and pos.get("quantity", 0) == 0):
                wallet_reason = (
                    f"wallet at capacity: deployed ${self.portfolio.invested_capital:.0f}"
                    f" / ${self.portfolio.effective_wallet:.0f}"
                )
                skipped = SkippedTrigger(
                    ticker=event.ticker,
                    trigger_type=event.trigger_type,
                    trigger_detail=event.trigger_detail,
                    reason=wallet_reason,
                    created_at=datetime.now(timezone.utc),
                )
                session.add(skipped)
                await session.commit()
                logger.debug(f"Skipped {event.ticker}: {wallet_reason}")
                return

            # Context normalization
            normalized_features = None
            if self.settings.signal.normalize_context:
                from app.engine.normalizer import compute_normalized_features
                nf = await compute_normalized_features(
                    event=event,
                    session=session,
                    portfolio=self.portfolio,
                    settings=self.settings,
                    signal_strength=signal_strength or 0.0,
                )
                normalized_features = {
                    "price_momentum": nf.price_momentum,
                    "technical_alignment": nf.technical_alignment,
                    "sentiment_composite": nf.sentiment_composite,
                    "portfolio_pressure": nf.portfolio_pressure,
                    "signal_strength": nf.signal_strength,
                }

            # Bulk-fetch prices for all held positions (avoids N+1 per-ticker queries)
            held_positions: list[HeldPosition] = []
            if self.portfolio.positions:
                held_tickers = list(self.portfolio.positions.keys())
                price_rows = await session.execute(
                    select(PriceCache.ticker, PriceCache.price).where(
                        PriceCache.ticker.in_(held_tickers)
                    )
                )
                held_price_map = {row.ticker: float(row.price) for row in price_rows}
                for tkr, p in self.portfolio.positions.items():
                    tkr_price = held_price_map.get(tkr) or p["avg_cost"]
                    held_positions.append(HeldPosition(
                        ticker=tkr,
                        quantity=p["quantity"],
                        avg_cost=p["avg_cost"],
                        current_price=tkr_price,
                        unrealized_pnl=p["quantity"] * (tkr_price - p["avg_cost"]),
                    ))

            wallet_remaining = max(
                0.0,
                self.portfolio.effective_wallet - self.portfolio.invested_capital,
            )
            # Signal-specific sentiment: reddit events carry .score, news events carry .sentiment_score
            signal_sentiment = getattr(event, "score", None) or getattr(event, "sentiment_score", None)

            # Calibration summary for LLM context
            calibration_summary = ""
            if self.settings.signal.calibration_enabled and self._calibration_tracker is not None:
                calibration_summary = await self._calibration_tracker.get_calibration_summary(
                    session, self.settings.signal.calibration_lookback_days
                )

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
                signal_sentiment=signal_sentiment,
                wallet_remaining=wallet_remaining,
                portfolio_positions=held_positions,
                signal_strength=signal_strength,
                normalized_features=normalized_features,
                calibration_summary=calibration_summary if calibration_summary else None,
            )

            # Run agentic decision loop (single-agent) or multi-agent pipeline
            from app.analysis.service import TechnicalAnalysisService
            from app.llm.prompt import build_user_message
            from app.llm.tool_executor import ToolExecutor
            ta_svc = self.ta_service or TechnicalAnalysisService()
            executor = ToolExecutor(portfolio=self.portfolio, ta_service=ta_svc)

            # Log what the LLM is about to receive
            user_msg = build_user_message(context)
            sig_score = (
                f"{context.signal_sentiment:+.3f}" if context.signal_sentiment is not None else "n/a"
            )
            logger.info(
                f"LLM input [{event.ticker}] trigger={event.trigger_type} "
                f"signal_sentiment={sig_score} pos={context.position_quantity:.0f}sh\n{user_msg}"
            )

            t0 = time.monotonic()
            if self.settings.llm.multi_agent:
                from app.llm.pipeline import AgentPipeline
                pipeline = AgentPipeline(
                    llm=self.llm,
                    settings=self.settings,
                    portfolio=self.portfolio,
                    ta_service=ta_svc,
                )
                decision, tool_calls = await pipeline.run(context, executor, session)
            else:
                decision, tool_calls = await self.llm.decide_with_tools(context, executor)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                f"Decision for {event.ticker}: {decision.action} x{decision.quantity} "
                f"— {decision.reasoning} ({len(tool_calls)} tool calls, {elapsed_ms}ms)"
            )

            # Confidence gate — downgrade low-confidence buy/sell to hold.
            # Strict less-than: a decision exactly at the gate threshold is allowed through.
            if (
                self.settings.signal.confidence_gate > 0
                and decision.confidence < self.settings.signal.confidence_gate
                and decision.action in ("buy", "sell")
            ):
                logger.info(
                    f"{event.ticker}: confidence gate {decision.confidence:.3f} < "
                    f"{self.settings.signal.confidence_gate:.3f}, converting to hold"
                )
                decision = Decision(
                    action="hold",
                    quantity=0,
                    reasoning=decision.reasoning,
                    confidence=decision.confidence,
                )

            # Persist agent reasoning trace
            trace: Optional[AgentReasoningTrace] = None
            if tool_calls:
                trace = AgentReasoningTrace(
                    ticker=event.ticker,
                    tool_calls=tool_calls,
                    duration_ms=elapsed_ms,
                    created_at=datetime.now(timezone.utc),
                )
                session.add(trace)
                await session.flush()  # get trace.id before Trade insert

            # Apply portfolio changes
            realized_pnl = None
            buy_blocked_reason: Optional[str] = None

            if decision.action == "buy" and decision.quantity > 0:
                if await self.portfolio.can_buy(event.ticker, current_price, decision.quantity, portfolio_value):
                    await self.portfolio.apply_buy(event.ticker, current_price, decision.quantity, session)
                else:
                    cost = decision.quantity * current_price
                    buy_blocked_reason = (
                        f"buy blocked: wanted {decision.quantity} shares "
                        f"(${cost:.0f}), wallet cap ${self.portfolio.effective_wallet:.0f}, "
                        f"already deployed ${self.portfolio.invested_capital:.0f}"
                    )
            elif decision.action == "sell" and decision.quantity > 0:
                realized_pnl = await self.portfolio.apply_sell(event.ticker, current_price, decision.quantity, session)

            committed_trade: Optional[Trade] = None
            if buy_blocked_reason:
                # Record as skipped — NOT as a trade — so load_from_db cash calc stays correct
                skipped = SkippedTrigger(
                    ticker=event.ticker,
                    trigger_type=event.trigger_type,
                    trigger_detail=event.trigger_detail,
                    reason=buy_blocked_reason,
                    created_at=datetime.now(timezone.utc),
                )
                session.add(skipped)
                logger.warning(f"{event.ticker}: {buy_blocked_reason}")
            else:
                # Only write to Trade table when the action was actually executed
                article_url = getattr(event, "url", None) or None
                committed_trade = Trade(
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
                    article_url=article_url,
                    llm_provider=self.llm.provider_name,
                    llm_model=getattr(self.llm, "model", None),
                    confidence=Decimal(str(round(decision.confidence, 3))),
                    signal_strength=Decimal(str(round(signal_strength, 4))) if signal_strength is not None else None,
                    agent_trace_id=trace.id if trace else None,
                    created_at=datetime.now(timezone.utc),
                )
                session.add(committed_trade)
                await session.flush()  # get committed_trade.id before calibration insert

            # Record calibration outcome (before commit so it's in the same transaction)
            if (
                self.settings.signal.calibration_enabled
                and committed_trade is not None
                and self._calibration_tracker is not None
            ):
                await self._calibration_tracker.record_decision(
                    committed_trade, signal_strength, session
                )

            await session.commit()

            # Update cooldown only when a trade was actually executed
            if committed_trade is not None:
                self._cooldowns[event.ticker] = datetime.now(timezone.utc)

            # Update last decision time
            if _update_last_decision:
                _update_last_decision(datetime.now(timezone.utc))

            # Broadcast via WebSocket
            if self._broadcast_callback:
                message = {
                    "type": "trade",
                    "ticker": event.ticker,
                    "action": decision.action,
                    "price": current_price,
                    "quantity": decision.quantity,
                    "reasoning": decision.reasoning,
                    "confidence": round(decision.confidence, 3),
                    "trigger_type": event.trigger_type,
                    "trigger_detail": event.trigger_detail,
                    "article_url": getattr(event, "url", None) or None,
                    "llm_provider": self.llm.provider_name,
                    "llm_model": getattr(self.llm, "model", ""),
                    "trade_id": committed_trade.id if committed_trade else None,
                    "agent_trace_id": trace.id if trace else None,
                    "tool_call_count": len(tool_calls),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                await self._broadcast_callback(message)

    async def take_portfolio_snapshot(self) -> None:
        """Called hourly by APScheduler."""
        async with async_session_factory() as session:
            portfolio_value = await self.portfolio.get_portfolio_value(session)
            snapshot = PortfolioSnapshot(
                total_value=Decimal(str(portfolio_value)),
                cash_balance=Decimal(str(self.portfolio.cash)),
                snapshot_at=datetime.now(timezone.utc),
            )
            session.add(snapshot)
            await session.commit()
            logger.info(f"Portfolio snapshot: ${portfolio_value:.2f}")

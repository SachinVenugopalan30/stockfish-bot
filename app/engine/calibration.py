"""Step 5 — Post-Decision Calibration: tracks how well past decisions performed."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DecisionOutcome, PriceCache

logger = logging.getLogger(__name__)


class CalibrationTracker:
    """Records trade decisions and evaluates their outcome after 1h / 24h."""

    # ------------------------------------------------------------------
    # Record
    # ------------------------------------------------------------------

    async def record_decision(self, trade, signal_strength, session: AsyncSession) -> None:
        """
        Create a DecisionOutcome row for *trade*.

        Does NOT commit — the caller is responsible for committing.
        """
        outcome = DecisionOutcome(
            trade_id=trade.id,
            ticker=trade.ticker,
            action=trade.action,
            confidence=trade.confidence,
            price_at_decision=trade.price_at_exec,
            signal_strength=(
                Decimal(str(signal_strength)) if signal_strength is not None else None
            ),
            decided_at=trade.created_at,
            # future evaluation fields — all None at creation time
            price_at_1h=None,
            price_at_24h=None,
            pct_change_1h=None,
            pct_change_24h=None,
            outcome_correct_1h=None,
            outcome_correct_24h=None,
            evaluated_at=None,
        )
        session.add(outcome)

    # ------------------------------------------------------------------
    # Evaluate
    # ------------------------------------------------------------------

    async def evaluate_pending(self, session: AsyncSession) -> None:
        """
        Fill price_at_1h / price_at_24h for pending DecisionOutcome rows.

        Commits its own changes (designed to be called from APScheduler).
        """
        now = datetime.now(timezone.utc)

        result = await session.execute(
            select(DecisionOutcome).where(DecisionOutcome.evaluated_at.is_(None))
        )
        outcomes = result.scalars().all()

        if not outcomes:
            return

        # Prefetch all needed prices in a single query (avoids N+1 DB round-trips).
        tickers = {o.ticker for o in outcomes}
        price_rows = await session.execute(
            select(PriceCache.ticker, PriceCache.price).where(PriceCache.ticker.in_(tickers))
        )
        price_map: dict[str, Decimal] = {row.ticker: row.price for row in price_rows}

        for outcome in outcomes:
            decided_at = outcome.decided_at
            # Make decided_at timezone-aware if it is naive (stored as UTC)
            if decided_at.tzinfo is None:
                decided_at = decided_at.replace(tzinfo=timezone.utc)

            age = now - decided_at

            # Fetch current price once per outcome (reused for both 1h and 24h windows).
            current_price = price_map.get(outcome.ticker)

            for hours, price_attr, pct_attr, correct_attr in [
                (1,  "price_at_1h",  "pct_change_1h",  "outcome_correct_1h"),
                (24, "price_at_24h", "pct_change_24h", "outcome_correct_24h"),
            ]:
                if age >= timedelta(hours=hours) and getattr(outcome, price_attr) is None:
                    if current_price is not None and outcome.price_at_decision is not None:
                        pct = (
                            (current_price - outcome.price_at_decision)
                            / outcome.price_at_decision
                            * 100
                        )
                        setattr(outcome, price_attr, current_price)
                        setattr(outcome, pct_attr, pct)
                        setattr(outcome, correct_attr, self._is_correct(outcome.action, pct))

            # Mark as evaluated only when BOTH 1h and 24h data are populated.
            # This prevents permanently closing a row where price_at_1h was
            # never filled (e.g. PriceCache was missing at the 1h mark).
            if outcome.price_at_1h is not None and outcome.price_at_24h is not None:
                outcome.evaluated_at = now

        await session.commit()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    async def get_calibration_summary(
        self, session: AsyncSession, lookback_days: int
    ) -> str:
        """
        Return a formatted accuracy-summary string for the past *lookback_days* days.
        Returns "" if no evaluated outcomes exist.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        result = await session.execute(
            select(DecisionOutcome).where(
                DecisionOutcome.decided_at >= cutoff,
                DecisionOutcome.outcome_correct_1h.is_not(None),
            )
        )
        rows = result.scalars().all()

        if not rows:
            return ""

        total = len(rows)

        # ---- Overall 1h ----
        correct_1h = sum(1 for r in rows if r.outcome_correct_1h)
        pct_1h = int(round(correct_1h / total * 100))

        # ---- Overall 24h ----
        rows_24h = [r for r in rows if r.outcome_correct_24h is not None]
        correct_24h = sum(1 for r in rows_24h if r.outcome_correct_24h)
        total_24h = len(rows_24h)
        pct_24h = int(round(correct_24h / total_24h * 100)) if total_24h else 0

        # ---- By action 1h + 24h ----
        def _action_stat(action: str) -> str:
            subset = [r for r in rows if r.action == action]
            t = len(subset)
            if not subset:
                return f"{action.upper()} 1h=0% 24h=0% (0)"
            c1h = sum(1 for r in subset if r.outcome_correct_1h)
            p1h = int(round(c1h / t * 100))
            subset_24h = [r for r in subset if r.outcome_correct_24h is not None]
            c24h = sum(1 for r in subset_24h if r.outcome_correct_24h)
            t24h = len(subset_24h)
            p24h = int(round(c24h / t24h * 100)) if t24h else 0
            return f"{action.upper()} 1h={p1h}% 24h={p24h}% ({t})"

        by_action = " | ".join(_action_stat(a) for a in ("buy", "sell", "hold"))

        # ---- By confidence bucket 1h ----
        def _conf_stat(label: str, low: float, high: float) -> str:
            subset = [
                r
                for r in rows
                if r.confidence is not None
                and low <= float(r.confidence) < high
            ]
            if not subset:
                return f"{label} 1h=n/a"
            c = sum(1 for r in subset if r.outcome_correct_1h)
            t = len(subset)
            p = int(round(c / t * 100))
            return f"{label} 1h={p}% ({c}/{t})"

        high_stat = _conf_stat("High(>0.7)", 0.7, float("inf"))
        med_stat = _conf_stat("Med(0.4-0.7)", 0.4, 0.7)
        low_stat = _conf_stat("Low(<0.4)", 0.0, 0.4)
        by_conf = " | ".join([high_stat, med_stat, low_stat])

        lines = [
            f"=== CALIBRATION (last {lookback_days} days, {total} decisions) ===",
            f"  Accuracy: 1h={pct_1h}% ({correct_1h}/{total}) | 24h={pct_24h}% ({correct_24h}/{total_24h})",
            f"  By action: {by_action}",
            f"  By confidence: {by_conf}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Scheduler wrapper
    # ------------------------------------------------------------------

    async def evaluate_pending_job(self) -> None:
        """APScheduler entry-point — opens its own session."""
        from app.database import async_session_factory

        async with async_session_factory() as session:
            await self.evaluate_pending(session)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_correct(action: str, pct_change: Decimal) -> bool:
        pct = float(pct_change)
        # Breakeven (pct == 0.0) is treated as incorrect for buy/sell
        if action == "buy":
            return pct > 0
        if action == "sell":
            return pct < 0
        # hold: correct when price barely moved (within ±1%)
        return abs(pct) <= 1.0

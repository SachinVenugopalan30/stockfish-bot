"""
risk_context.py — pre-computes a rich, structured risk data package for the Risk Agent.

Queries portfolio state, sector concentration, price volatility, technical indicators,
sentiment history, and trade history from the DB and formats them as a readable text block.
"""
import logging
import math
from datetime import datetime, timedelta

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.engine.portfolio import PortfolioManager
from app.models.market_data import (
    PriceCache,
    PriceHistory,
    SentimentScore,
    TechnicalIndicator,
    TickerMetadata,
)
from app.models.trades import Trade

logger = logging.getLogger(__name__)


def _fmt_pnl(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}${value:,.2f}"


def _fmt_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


async def build_risk_context(
    ticker: str,
    portfolio: PortfolioManager,
    session: AsyncSession,
    settings: Settings,
) -> str:
    """
    Assembles a comprehensive risk context string for the Risk Agent.
    All queries are read-only and use the provided session.
    """
    ticker = ticker.upper()
    now = datetime.utcnow()
    lines: list[str] = [f"═══ RISK CONTEXT FOR {ticker} ═══\n"]

    # ── 1. Portfolio Exposure ─────────────────────────────────────────────────
    lines.append("📊 PORTFOLIO EXPOSURE")
    portfolio_value = await portfolio.get_portfolio_value(session)
    cash = portfolio.cash
    invested = portfolio.invested_capital
    wallet_cap = portfolio.effective_wallet
    wallet_used_pct = (invested / wallet_cap * 100) if wallet_cap > 0 else 0.0

    lines.append(f"  Cash: ${cash:,.2f} | Invested: ${invested:,.2f} | Portfolio value: ${portfolio_value:,.2f}")
    lines.append(f"  Wallet utilization: {wallet_used_pct:.1f}% of ${wallet_cap:,.0f} cap")
    lines.append(f"  Open positions: {len(portfolio.positions)}")

    # Per-position breakdown with sector lookup
    if portfolio.positions:
        lines.append("")
        lines.append("  Position breakdown:")
        sector_buckets: dict[str, float] = {}

        # Batch-fetch prices and sectors for all positions
        pos_tickers = list(portfolio.positions.keys())
        price_rows = await session.execute(
            select(PriceCache.ticker, PriceCache.price).where(PriceCache.ticker.in_(pos_tickers))
        )
        price_cache: dict[str, float] = {r.ticker: float(r.price) for r in price_rows.all() if r.price is not None}

        meta_rows = await session.execute(
            select(TickerMetadata.ticker, TickerMetadata.sector).where(TickerMetadata.ticker.in_(pos_tickers))
        )
        sector_cache: dict[str, str] = {r.ticker: (r.sector or "Unknown") for r in meta_rows.all()}

        for pos_ticker, pos in portfolio.positions.items():
            current_price = price_cache.get(pos_ticker, float(pos["avg_cost"]))
            qty = float(pos["quantity"])
            avg_cost = float(pos["avg_cost"])
            pos_value = qty * current_price
            unrealized = qty * (current_price - avg_cost)
            pos_pct = (pos_value / portfolio_value * 100) if portfolio_value > 0 else 0.0
            arrow = "▲" if unrealized >= 0 else "▼"

            lines.append(
                f"    {pos_ticker}: {qty:.0f} sh @ ${avg_cost:.2f} avg"
                f" → ${pos_value:,.0f} ({pos_pct:.1f}% of portfolio)"
                f" {arrow} {_fmt_pnl(unrealized)}"
            )

            sector = sector_cache.get(pos_ticker, "Unknown")
            sector_buckets[sector] = sector_buckets.get(sector, 0.0) + pos_value

        # Sector concentration
        if sector_buckets:
            lines.append("")
            lines.append("  Sector concentration:")
            for sector, val in sorted(sector_buckets.items(), key=lambda x: -x[1]):
                pct = (val / portfolio_value * 100) if portfolio_value > 0 else 0.0
                lines.append(f"    {sector}: ${val:,.0f} ({pct:.1f}% of portfolio)")
    else:
        lines.append("  No open positions.")

    # ── 2. Volatility from PriceHistory ──────────────────────────────────────
    lines.append("")
    lines.append("📈 PRICE VOLATILITY (last 24h)")
    cutoff_24h = now - timedelta(hours=24)
    price_rows = await session.execute(
        select(PriceHistory.price, PriceHistory.recorded_at)
        .where(and_(PriceHistory.ticker == ticker, PriceHistory.recorded_at >= cutoff_24h))
        .order_by(PriceHistory.recorded_at)
    )
    price_records = price_rows.all()

    if len(price_records) >= 2:
        prices = [float(r.price) for r in price_records]
        high = max(prices)
        low = min(prices)
        spread_pct = ((high - low) / low * 100) if low > 0 else 0.0
        # Compute std dev of period-to-period returns
        returns = [(prices[i] - prices[i - 1]) / prices[i - 1] * 100 for i in range(1, len(prices))]
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance)
        lines.append(f"  Price range: ${low:.2f} — ${high:.2f} ({spread_pct:.1f}% spread)")
        lines.append(f"  Std dev of returns: {std_dev:.3f}% per tick ({len(price_records)} data points)")
        vol_label = "HIGH" if std_dev > 1.5 else "MODERATE" if std_dev > 0.5 else "LOW"
        lines.append(f"  Volatility assessment: {vol_label}")
    else:
        lines.append("  Insufficient price history for volatility calculation.")

    # ── 3. Technical Indicators ───────────────────────────────────────────────
    lines.append("")
    lines.append("⚡ TECHNICAL INDICATORS (latest computed)")
    ind_rows = await session.execute(
        select(TechnicalIndicator)
        .where(TechnicalIndicator.ticker == ticker)
        .order_by(desc(TechnicalIndicator.computed_at))
        .limit(20)  # fetch recent batch, dedupe by type below
    )
    all_indicators = ind_rows.scalars().all()
    # Keep only the most recent value per type
    latest_by_type: dict[str, TechnicalIndicator] = {}
    for ind in all_indicators:
        if ind.indicator_type not in latest_by_type:
            latest_by_type[ind.indicator_type] = ind

    if latest_by_type:
        rsi = latest_by_type.get("RSI")
        macd = latest_by_type.get("MACD")
        sma = latest_by_type.get("SMA_20")
        ema = latest_by_type.get("EMA_12")
        boll = latest_by_type.get("BOLLINGER")

        if rsi:
            v = float(rsi.value)
            risk_note = " ⚠ OVERBOUGHT — elevated sell risk" if v >= 70 else " ⚠ OVERSOLD — bounce possible" if v <= 30 else ""
            lines.append(f"  RSI: {v:.1f} ({rsi.signal}){risk_note}")
        if macd:
            v = float(macd.value)
            sign = "+" if v >= 0 else ""
            lines.append(f"  MACD: {sign}{v:.4f} ({macd.signal})")
        if sma:
            lines.append(f"  SMA_20: ${float(sma.value):.2f}")
        if ema:
            lines.append(f"  EMA_12: ${float(ema.value):.2f}")
        if boll:
            # BOLLINGER value stores the bandwidth; compute context from price
            lines.append(f"  Bollinger bandwidth: {float(boll.value):.4f} ({boll.signal})")
        if rsi and macd:
            # Combined signal summary
            both_bearish = float(rsi.value) >= 70 and float(macd.value) < 0
            both_bullish = float(rsi.value) <= 30 and float(macd.value) > 0
            if both_bearish:
                lines.append("  ⚠ COMBINED SIGNAL: RSI overbought + MACD bearish — strong sell pressure")
            elif both_bullish:
                lines.append("  ✓ COMBINED SIGNAL: RSI oversold + MACD bullish — strong buy signal")
    else:
        lines.append("  No technical indicators computed yet.")

    # ── 4. Sentiment Snapshot ─────────────────────────────────────────────────
    lines.append("")
    lines.append("📰 SENTIMENT SNAPSHOT (last 10 scores)")
    sent_rows = await session.execute(
        select(SentimentScore)
        .where(SentimentScore.ticker == ticker)
        .order_by(desc(SentimentScore.recorded_at))
        .limit(10)
    )
    sent_records = sent_rows.scalars().all()

    if sent_records:
        scores = [float(r.score) for r in sent_records if r.score is not None]
        if scores:
            avg_score = sum(scores) / len(scores)
            pos_count = sum(1 for s in scores if s > 0.1)
            neg_count = sum(1 for s in scores if s < -0.1)
            neu_count = len(scores) - pos_count - neg_count
            sent_label = "POSITIVE" if avg_score > 0.1 else "NEGATIVE" if avg_score < -0.1 else "NEUTRAL"
            lines.append(f"  Average score: {avg_score:+.3f} ({sent_label})")
            lines.append(f"  Breakdown: {pos_count} positive, {neu_count} neutral, {neg_count} negative")
            if avg_score < -0.3:
                lines.append("  ⚠ STRONG NEGATIVE SENTIMENT — elevated sell risk")
            elif avg_score > 0.3:
                lines.append("  ✓ STRONG POSITIVE SENTIMENT — supportive of buy")
    else:
        lines.append("  No sentiment data available.")

    # ── 5. Trade History Stats ────────────────────────────────────────────────
    lines.append("")
    lines.append("📋 TRADE HISTORY (last 7 days, all tickers)")
    cutoff_7d = now - timedelta(days=7)
    all_trades_rows = await session.execute(
        select(Trade)
        .where(Trade.created_at >= cutoff_7d)
        .order_by(desc(Trade.created_at))
    )
    all_trades = all_trades_rows.scalars().all()

    buys = [t for t in all_trades if t.action == "buy"]
    sells = [t for t in all_trades if t.action == "sell"]
    winning_sells = [t for t in sells if t.realized_pnl is not None and float(t.realized_pnl) > 0]
    win_rate = (len(winning_sells) / len(sells) * 100) if sells else 0.0
    avg_sell_pnl = (
        sum(float(t.realized_pnl) for t in sells if t.realized_pnl is not None) / len(sells)
        if sells else 0.0
    )
    avg_conf = (
        sum(float(t.confidence) for t in all_trades if t.confidence is not None) /
        max(1, sum(1 for t in all_trades if t.confidence is not None))
    )

    lines.append(f"  Total: {len(all_trades)} trades ({len(buys)} buys, {len(sells)} sells)")
    if sells:
        lines.append(f"  Win rate: {win_rate:.0f}% ({len(winning_sells)}/{len(sells)} profitable sells)")
        lines.append(f"  Avg realized P&L per sell: {_fmt_pnl(avg_sell_pnl)}")
    lines.append(f"  Avg confidence: {avg_conf:.2f}")

    # Per-ticker trade history
    ticker_trades = [t for t in all_trades if t.ticker == ticker]
    lines.append("")
    lines.append(f"  Recent trades on {ticker} (last 7d):")
    if ticker_trades:
        for t in ticker_trades[:5]:
            qty = f"{float(t.quantity):.0f}" if t.quantity else "?"
            price = f"${float(t.price_at_exec):.2f}" if t.price_at_exec else "?"
            conf = f"{float(t.confidence):.2f}" if t.confidence else "?"
            pnl_str = f" | P&L: {_fmt_pnl(float(t.realized_pnl))}" if t.realized_pnl else ""
            age_h = (now - t.created_at.replace(tzinfo=None)).total_seconds() / 3600
            age_str = f"{age_h:.0f}h ago" if age_h < 48 else f"{age_h/24:.0f}d ago"
            lines.append(f"    {t.action.upper()} {qty} @ {price} — {age_str} (conf:{conf}){pnl_str}")
    else:
        lines.append(f"    No trades on {ticker} in the last 7 days.")

    # ── 6. Position Sizing Constraints ────────────────────────────────────────
    lines.append("")
    lines.append("🎯 POSITION SIZING CONSTRAINTS")
    max_pct = settings.portfolio.max_position_pct
    max_allowed = portfolio_value * max_pct / 100

    # Current exposure to this ticker (reuse price already batch-fetched above if held)
    current_exposure = 0.0
    if ticker in portfolio.positions:
        pos = portfolio.positions[ticker]
        # price_cache is defined whenever portfolio.positions is non-empty (see loop above)
        cur_price = price_cache.get(ticker, float(pos["avg_cost"]))
        current_exposure = float(pos["quantity"]) * cur_price

    remaining_capacity = max(0.0, max_allowed - current_exposure)
    exposure_pct = (current_exposure / portfolio_value * 100) if portfolio_value > 0 else 0.0

    lines.append(f"  Max position: {max_pct}% of portfolio = ${max_allowed:,.0f}")
    lines.append(f"  Current exposure to {ticker}: ${current_exposure:,.0f} ({exposure_pct:.1f}%)")
    lines.append(f"  Remaining buy capacity for {ticker}: ${remaining_capacity:,.0f}")
    lines.append(f"  Wallet remaining (all tickers): ${portfolio.effective_wallet - invested:,.0f}")

    lines.append("")
    return "\n".join(lines)

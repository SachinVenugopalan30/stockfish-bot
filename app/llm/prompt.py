from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.llm.base import TradeContext


def format_features_block(features: dict) -> str:
    """Format normalized signal features as a display block."""
    return (
        "\n=== SIGNAL FEATURES (-1.0 to +1.0) ===\n"
        f"  Price momentum:      {features.get('price_momentum', 0):+.2f}\n"
        f"  Technical alignment: {features.get('technical_alignment', 0):+.2f}\n"
        f"  Sentiment composite: {features.get('sentiment_composite', 0):+.2f}\n"
        f"  Portfolio pressure:  {features.get('portfolio_pressure', 0):+.2f}\n"
        f"  Signal strength:     {features.get('signal_strength', 0):.2f}\n"
        "Note: positive = bullish/room-to-buy, negative = bearish/should-reduce\n"
    )


SYSTEM_PROMPT = """You are a paper trading analyst bot. You make trading decisions based on market signals.

Rules:
- Paper trading only — no real money
- Never exceed {max_position_pct}% of portfolio in a single position
- Never spend more than the available wallet remaining shown in the context
- Target a DIVERSIFIED portfolio of 5–10 different tickers — never concentrate heavily in one or two stocks
- Keep individual buy sizes modest (1–3% of portfolio) to preserve room for future opportunities

SELL rules — act on these, do not hold through them:
- SELL if you hold a position and the signal is negative (FinBERT score < -0.2) — lock in profits or cut losses
- SELL if a price drop triggered this event and you hold the stock at a profit — take gains before they erode
- SELL if the signal sentiment is strongly negative (< -0.5) regardless of P&L — protect capital
- Selling a full position is fine; set quantity = all shares held

- Respond ONLY with valid JSON in this exact format:
  {{"action": "buy"|"sell"|"hold", "quantity": <number>, "reasoning": "<one sentence>", "confidence": <0.0-1.0>}}
- quantity is number of shares (0 for hold)
- Keep reasoning to one sentence, focused on the triggering signal"""

AGENTIC_SYSTEM_PROMPT = """You are an autonomous trading analyst agent. A market signal has been triggered and you must investigate it before making a trading decision.

Rules:
- Paper trading only — no real money
- Never exceed {max_position_pct}% of portfolio in a single position
- Never spend more than the wallet remaining shown in the context
- Target a DIVERSIFIED portfolio of 5–10 different tickers — never concentrate heavily in one or two stocks
- Keep individual buy sizes modest (1–3% of portfolio) to preserve room for future opportunities across different tickers

SELL rules — act on these decisively:
- SELL if you hold a position and the signal is negative (FinBERT score < -0.2) — lock in profits or cut losses
- SELL if a price drop triggered this event and you hold the stock at a profit — take gains before they erode further
- SELL if signal sentiment is strongly negative (< -0.5) regardless of P&L — capital protection matters
- Selling a full position is fine; set quantity = all shares held

Workflow:
- ALWAYS call at least one research tool (get_price_history, get_technical_indicators, get_sentiment, or get_recent_news) before submitting
- Call check_portfolio to see your current holdings and P&L before sizing any buy or sell
- After gathering enough evidence, call submit_decision with your final action
- quantity is number of shares (0 for hold)"""


def build_user_message(context: "TradeContext") -> str:
    # ── Current position in the triggered ticker ──────────────────────────────
    position_info = "No current position."
    if context.position_quantity > 0:
        cost_basis = context.position_quantity * context.position_avg_cost
        unrl = context.position_quantity * (context.current_price - context.position_avg_cost)
        sign = "+" if unrl >= 0 else ""
        position_info = (
            f"Current position: {context.position_quantity} shares "
            f"@ avg ${context.position_avg_cost:.2f} "
            f"(cost basis ${cost_basis:.2f}, unrealized P&L {sign}${unrl:.2f})"
        )

    # ── Full portfolio holdings ───────────────────────────────────────────────
    portfolio_positions = getattr(context, "portfolio_positions", [])
    if portfolio_positions:
        total_invested = sum(p.quantity * p.avg_cost for p in portfolio_positions)
        total_unrealized = sum(p.unrealized_pnl for p in portfolio_positions)
        unrl_sign = "+" if total_unrealized >= 0 else ""
        holdings_lines = []
        for p in portfolio_positions:
            unrl_sign_p = "+" if p.unrealized_pnl >= 0 else ""
            price_str = f"@ ${p.current_price:.2f}" if p.current_price > 0 else ""
            holdings_lines.append(
                f"  {p.ticker}: {p.quantity} shares {price_str} "
                f"(avg cost ${p.avg_cost:.2f}, P&L {unrl_sign_p}${p.unrealized_pnl:.2f})"
            )
        portfolio_section = (
            f"Portfolio Holdings ({len(portfolio_positions)} open position(s), "
            f"${total_invested:.2f} invested, {unrl_sign}${total_unrealized:.2f} total unrealized P&L):\n"
            + "\n".join(holdings_lines)
        )
    else:
        portfolio_section = "Portfolio Holdings: none — no open positions."

    # ── Position sizing ───────────────────────────────────────────────────────
    wallet_remaining = getattr(context, "wallet_remaining", context.cash_balance)
    max_by_pct = context.portfolio_value * (context.max_position_pct / 100)
    max_spend = min(max_by_pct, wallet_remaining)
    max_shares = int(max_spend / context.current_price) if context.current_price > 0 else 0
    suggested_spend = context.portfolio_value * 0.015
    suggested_shares = int(min(suggested_spend, wallet_remaining) / context.current_price) if context.current_price > 0 else 0

    # ── Sell opportunity callout ──────────────────────────────────────────────
    sell_alert = ""
    if context.position_quantity > 0:
        unrealized = context.position_quantity * (context.current_price - context.position_avg_cost)
        pnl_pct = ((context.current_price - context.position_avg_cost) / context.position_avg_cost * 100
                   if context.position_avg_cost > 0 else 0)
        pnl_sign = "+" if unrealized >= 0 else ""
        signal_score = getattr(context, "signal_sentiment", None)
        is_negative_signal = signal_score is not None and signal_score < -0.2
        is_price_drop = context.trigger_type == "price" and context.trigger_detail.startswith("-")

        if is_negative_signal or is_price_drop:
            sell_alert = (
                f"\n⚠️  SELL OPPORTUNITY: You hold {int(context.position_quantity)} shares of {context.ticker} "
                f"at avg ${context.position_avg_cost:.2f} "
                f"(unrealized P&L: {pnl_sign}${unrealized:.2f}, {pnl_sign}{pnl_pct:.1f}%).\n"
                f"   Signal is {'NEGATIVE' if is_negative_signal else 'a PRICE DROP'} — "
                f"consider selling {'to lock in profit' if unrealized > 0 else 'to limit further loss'}.\n"
            )

    # ── Signal sentiment line ─────────────────────────────────────────────────
    signal_sentiment = getattr(context, "signal_sentiment", None)
    if signal_sentiment is not None:
        label = "NEGATIVE" if signal_sentiment < -0.2 else ("POSITIVE" if signal_sentiment > 0.2 else "NEUTRAL")
        signal_sentiment_line = f"Signal FinBERT sentiment: {signal_sentiment:.2f} ({label})"
    else:
        signal_sentiment_line = f"Recent avg sentiment: {context.recent_sentiment:.2f} (-1=negative, 0=neutral, +1=positive)"

    # Normalized features block (when available)
    features_block = ""
    normalized_features = getattr(context, "normalized_features", None)
    if normalized_features:
        features_block = format_features_block(normalized_features)

    # Calibration summary (when available)
    calibration_summary = getattr(context, "calibration_summary", None)
    calibration_block = f"\n{calibration_summary}\n" if calibration_summary else ""

    return f"""Ticker: {context.ticker}
Current price: ${context.current_price:.2f}
{position_info}
{sell_alert}
{portfolio_section}

Wallet remaining for new positions: ${wallet_remaining:.2f}
Max position size ({context.max_position_pct}% of portfolio, capped by wallet): ${max_spend:.2f} ({max_shares} shares max)
Suggested buy size (1.5% of portfolio for diversification): {suggested_shares} shares (${suggested_shares * context.current_price:.2f})
{signal_sentiment_line}
{features_block}{calibration_block}
Trigger: {context.trigger_type.upper()}
Signal: {context.trigger_detail}

Respond with JSON only."""

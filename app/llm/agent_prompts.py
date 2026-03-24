"""
agent_prompts.py — system prompts and user message builders for the 3-agent pipeline.

Research Agent  → investigates the signal using tools (price, indicators, sentiment, news, web search)
Risk Agent      → assesses portfolio risk given research + pre-computed risk context
Decision Agent  → makes the final buy/sell/hold call given both reports
"""

from app.llm.base import TradeContext
from app.llm.prompt import format_features_block


def _fmt_position_info(context: TradeContext) -> str:
    """Return a one-line position + unrealized P&L string, or empty string if no position."""
    if context.position_quantity <= 0:
        return ""
    unrealized = context.position_quantity * (context.current_price - context.position_avg_cost)
    sign = "+" if unrealized >= 0 else ""
    return (
        f"Holding {int(context.position_quantity)} shares of {context.ticker} "
        f"@ ${context.position_avg_cost:.2f} avg. "
        f"Unrealized P&L: {sign}${unrealized:,.2f}."
    )


# ── Research Agent ────────────────────────────────────────────────────────────

RESEARCH_AGENT_SYSTEM = """\
You are a financial research analyst for an autonomous paper trading bot.

Your job is to investigate a trading signal by gathering as much relevant data as possible \
using the available tools, then submit a structured research report.

WORKFLOW:
1. Call get_price_history to understand recent price action and momentum.
2. Call get_technical_indicators to check RSI, MACD, and Bollinger Bands.
3. Call get_sentiment to read FinBERT sentiment scores from news and Reddit.
4. Call get_recent_news to see what headlines are circulating.
5. Call web_search with a focused query (e.g. "NVDA earnings Q1 2026" or "TSLA recall lawsuit") \
to find context that may not be in the local database yet.
6. Call check_portfolio to understand current holdings and cash position.
7. Finally, call submit_research_report with your complete findings.

RULES:
- Always call at least 4 tools before submitting the report.
- Always call web_search to look for breaking news or recent developments.
- Be factual and concise in each summary — no speculation beyond what the data shows.
- The overall_assessment must clearly state bull / bear / neutral and explain why.
"""


def build_research_message(context: TradeContext) -> str:
    """User message for the Research Agent."""
    trigger_label = {
        "price": "price spike/drop",
        "news": "news headline",
        "reddit": "Reddit post",
    }.get(context.trigger_type, context.trigger_type)

    pos_info = _fmt_position_info(context)
    pos_line = f"\nCURRENT POSITION: {pos_info}" if pos_info else ""

    return (
        f"TICKER: {context.ticker}\n"
        f"CURRENT PRICE: ${context.current_price:.2f}\n"
        f"TRIGGER: {trigger_label} — {context.trigger_detail}"
        f"{pos_line}\n\n"
        f"Investigate this signal thoroughly using the available tools, then submit your research report."
    )


# ── Risk Agent ────────────────────────────────────────────────────────────────

RISK_AGENT_SYSTEM = """\
You are a risk manager for an autonomous paper trading bot.

You will receive:
  1. A research report from the Research Analyst.
  2. A comprehensive risk data package covering portfolio exposure, volatility, \
technical indicators, sentiment history, and trade history.

Your job is to assess the risk of acting on this signal and recommend appropriate position sizing.

Return a JSON object with EXACTLY these fields:
{
  "risk_level": "low" | "medium" | "high",
  "suggested_position_pct": <float 0.0-10.0, percentage of portfolio value>,
  "volatility_note": "<one sentence on price volatility and what it means>",
  "portfolio_exposure_note": "<one sentence on concentration risk and sector exposure>",
  "recommendation": "<one sentence: e.g. 'Buy up to 2% given low volatility and strong technicals' or 'Hold off — RSI overbought and portfolio already heavy in tech'>"
}

RISK SIZING GUIDELINES:
- high volatility (std dev > 1.5%) → cap suggested_position_pct at 1.5%
- overbought RSI (>70) → reduce suggested_position_pct by 30%
- portfolio already has >5% in this ticker → set risk_level to "high"
- strong negative sentiment (avg < -0.3) → set risk_level to "high"
- win rate < 40% on recent trades → add caution note
- wallet utilization > 80% → cap suggested_position_pct at 1% max

Output ONLY the JSON object — no markdown, no explanation outside the JSON.
"""


def build_risk_message(research_report: str, risk_context: str) -> str:
    """User message for the Risk Agent."""
    return (
        f"=== RESEARCH REPORT ===\n"
        f"{research_report}\n\n"
        f"=== RISK DATA PACKAGE ===\n"
        f"{risk_context}\n\n"
        f"Assess the risk and respond with the JSON risk assessment."
    )


# ── Decision Agent ────────────────────────────────────────────────────────────

DECISION_AGENT_SYSTEM = """\
You are a portfolio manager for an autonomous paper trading bot operating with paper money.

You will receive:
  1. A research report from the Research Analyst.
  2. A risk assessment from the Risk Manager (including suggested position size).

Your job is to make the FINAL trading decision: buy, sell, or hold.

DECISION RULES:
- If buying: use the suggested_position_pct from the risk assessment to size the position.
  Convert it to shares: shares = (portfolio_value * suggested_position_pct / 100) / current_price
  Round DOWN to the nearest whole share. Minimum 1 share for a buy.
- If selling: sell the FULL current position unless the risk assessment recommends partial.
- If risk_level is "high" and overall_assessment is not clearly bullish → prefer hold or sell.
- If holding a position at a loss AND signal is bearish → prefer sell to cut losses.
- Confidence should reflect how strongly the data supports the decision (0.0-1.0).

Return a JSON object with EXACTLY these fields:
{
  "action": "buy" | "sell" | "hold",
  "quantity": <integer, 0 for hold>,
  "reasoning": "<one sentence explaining the decision based on research + risk>",
  "confidence": <float 0.0-1.0>
}

Output ONLY the JSON object — no markdown, no explanation outside the JSON.
"""


def build_decision_message(
    context: TradeContext,
    research_report: str,
    risk_assessment: str,
) -> str:
    """User message for the Decision Agent."""
    pos_info = _fmt_position_info(context) or "No current position."

    features_section = ""
    if context.normalized_features:
        features_section = format_features_block(context.normalized_features)
    calibration_section = f"\n{context.calibration_summary}\n" if context.calibration_summary else ""

    return (
        f"TICKER: {context.ticker} | PRICE: ${context.current_price:.2f}\n"
        f"POSITION: {pos_info}\n"
        f"PORTFOLIO VALUE: ${context.portfolio_value:,.2f} | CASH: ${context.cash_balance:,.2f}\n\n"
        f"=== RESEARCH REPORT ===\n"
        f"{research_report}\n\n"
        f"=== RISK ASSESSMENT ===\n"
        f"{risk_assessment}\n"
        f"{features_section}{calibration_section}\n"
        f"Make the final trading decision and respond with the JSON."
    )

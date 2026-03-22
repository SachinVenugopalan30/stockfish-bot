SYSTEM_PROMPT = """You are a paper trading analyst bot. You make trading decisions based on market signals.

Rules:
- Paper trading only — no real money
- Never exceed {max_position_pct}% of portfolio in a single position
- Respond ONLY with valid JSON in this exact format:
  {{"action": "buy"|"sell"|"hold", "quantity": <number>, "reasoning": "<one sentence>", "confidence": <0.0-1.0>}}
- quantity is number of shares (0 for hold)
- Keep reasoning to one sentence, focused on the triggering signal
- Be conservative — prefer hold when uncertain"""

def build_user_message(context: "TradeContext") -> str:
    position_info = "No current position."
    if context.position_quantity > 0:
        position_info = f"Current position: {context.position_quantity} shares @ avg ${context.position_avg_cost:.2f}"

    max_spend = context.portfolio_value * (context.max_position_pct / 100)
    max_shares = int(max_spend / context.current_price) if context.current_price > 0 else 0

    return f"""Ticker: {context.ticker}
Current price: ${context.current_price:.2f}
{position_info}
Cash available: ${context.cash_balance:.2f}
Max position value ({context.max_position_pct}% of portfolio): ${max_spend:.2f} ({max_shares} shares max)
Recent sentiment score: {context.recent_sentiment:.2f} (-1=negative, 0=neutral, +1=positive)

Trigger: {context.trigger_type.upper()}
Signal: {context.trigger_detail}

Respond with JSON only."""

import pytest
from datetime import datetime
from app.llm.base import TradeContext, Decision, LLMProvider


def test_trade_context_defaults():
    ctx = TradeContext(
        ticker="AAPL",
        current_price=150.0,
        trigger_type="price",
        trigger_detail="+3% in 5min",
    )
    assert ctx.position_quantity == 0.0
    assert ctx.cash_balance == 100000.0
    assert ctx.max_position_pct == 10.0
    assert ctx.recent_sentiment == 0.0


def test_trade_context_with_position():
    ctx = TradeContext(
        ticker="NVDA",
        current_price=500.0,
        trigger_type="news",
        trigger_detail="Earnings beat",
        position_quantity=10.0,
        position_avg_cost=480.0,
    )
    assert ctx.position_quantity == 10.0
    assert ctx.position_avg_cost == 480.0


def test_decision_fields():
    d = Decision(action="buy", quantity=5.0, reasoning="Strong signal")
    assert d.action == "buy"
    assert d.quantity == 5.0
    assert d.confidence == 0.5  # default


def test_decision_all_actions():
    for action in ["buy", "sell", "hold"]:
        d = Decision(action=action, quantity=0.0, reasoning="test")
        assert d.action == action


def test_llm_provider_is_abstract():
    with pytest.raises(TypeError):
        LLMProvider()  # type: ignore

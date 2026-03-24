import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.base import TradeContext
from app.llm.claude import ClaudeProvider


@pytest.fixture
def claude():
    return ClaudeProvider(model="claude-sonnet-4-6")


@pytest.fixture
def trade_context():
    return TradeContext(
        ticker="NVDA",
        current_price=500.0,
        trigger_type="news",
        trigger_detail="Earnings beat estimates",
    )


async def test_claude_decision(claude, trade_context):
    # Actual code: message.content[0].text.strip() -> json.loads(raw)
    mock_content = MagicMock()
    mock_content.text = json.dumps({
        "action": "buy",
        "quantity": 5,
        "reasoning": "Earnings beat suggests continued growth",
        "confidence": 0.85,
    })
    mock_message = MagicMock()
    mock_message.content = [mock_content]

    with patch.object(claude.client.messages, "create", new=AsyncMock(return_value=mock_message)):
        decision = await claude.decide(trade_context)

    assert decision.action == "buy"
    assert decision.quantity == 5.0
    assert decision.confidence == 0.85


def test_claude_provider_name(claude):
    assert claude.provider_name == "claude"

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.base import TradeContext
from app.llm.openai_provider import OpenAIProvider


@pytest.fixture
def openai_provider():
    return OpenAIProvider(model="gpt-4o-mini")


@pytest.fixture
def trade_context():
    return TradeContext(
        ticker="TSLA",
        current_price=200.0,
        trigger_type="reddit",
        trigger_detail="WSB post going viral",
    )


async def test_openai_decision(openai_provider, trade_context):
    # Actual code: response.choices[0].message.content.strip() -> json.loads(raw)
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({
        "action": "sell",
        "quantity": 3,
        "reasoning": "Social media hype without fundamentals",
        "confidence": 0.6,
    })
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch.object(
        openai_provider.client.chat.completions,
        "create",
        new=AsyncMock(return_value=mock_response),
    ):
        decision = await openai_provider.decide(trade_context)

    assert decision.action == "sell"
    assert decision.quantity == 3.0


def test_openai_provider_name(openai_provider):
    assert openai_provider.provider_name == "openai"

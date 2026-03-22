import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from app.llm.gemini import GeminiProvider
from app.llm.base import TradeContext


@pytest.fixture
def gemini_provider():
    return GeminiProvider(model="gemini-2.0-flash")


@pytest.fixture
def trade_context():
    return TradeContext(
        ticker="AMZN",
        current_price=180.0,
        trigger_type="price",
        trigger_detail="-3% in 5min",
    )


async def test_gemini_decision(gemini_provider, trade_context):
    # Actual code: response.text.strip() -> strip markdown fences -> json.loads
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "action": "hold",
        "quantity": 0,
        "reasoning": "Downward movement may be temporary",
        "confidence": 0.4,
    })

    with patch.object(
        gemini_provider.client.aio.models,
        "generate_content",
        new=AsyncMock(return_value=mock_response),
    ):
        decision = await gemini_provider.decide(trade_context)

    assert decision.action == "hold"


async def test_gemini_strips_markdown(gemini_provider, trade_context):
    # Actual stripping logic:
    #   raw.startswith("```") -> raw = raw.split("```")[1]
    #   if raw.startswith("json"): raw = raw[4:]
    inner = json.dumps({
        "action": "buy",
        "quantity": 2,
        "reasoning": "Dip buying opportunity",
        "confidence": 0.7,
    })
    mock_response = MagicMock()
    mock_response.text = "```json\n" + inner + "\n```"

    with patch.object(
        gemini_provider.client.aio.models,
        "generate_content",
        new=AsyncMock(return_value=mock_response),
    ):
        decision = await gemini_provider.decide(trade_context)

    assert decision.action == "buy"


def test_gemini_provider_name(gemini_provider):
    assert gemini_provider.provider_name == "gemini"

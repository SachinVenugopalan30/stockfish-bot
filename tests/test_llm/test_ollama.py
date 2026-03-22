import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from app.llm.ollama import OllamaProvider
from app.llm.base import TradeContext


@pytest.fixture
def ollama():
    return OllamaProvider(model="llama3", host="http://localhost:11434")


@pytest.fixture
def trade_context():
    return TradeContext(
        ticker="AAPL",
        current_price=150.0,
        trigger_type="price",
        trigger_detail="+2.5% in 5min",
        cash_balance=100000.0,
        portfolio_value=100000.0,
    )


async def test_ollama_buy_decision(ollama, trade_context):
    # The actual OllamaProvider does:
    #   raw = response.json()["message"]["content"].strip()
    # so content must be a JSON string, not a dict.
    content_str = json.dumps({
        "action": "buy",
        "quantity": 10,
        "reasoning": "Strong upward momentum detected",
        "confidence": 0.8,
    })
    mock_response = MagicMock()
    mock_response.json.return_value = {"message": {"content": content_str}}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        decision = await ollama.decide(trade_context)

    assert decision.action == "buy"
    assert decision.quantity == 10.0
    assert decision.confidence == 0.8


async def test_ollama_hold_decision(ollama, trade_context):
    content_str = json.dumps({
        "action": "hold",
        "quantity": 0,
        "reasoning": "Uncertain signal",
        "confidence": 0.3,
    })
    mock_response = MagicMock()
    mock_response.json.return_value = {"message": {"content": content_str}}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        decision = await ollama.decide(trade_context)

    assert decision.action == "hold"
    assert decision.quantity == 0.0


def test_ollama_provider_name(ollama):
    assert ollama.provider_name == "ollama"

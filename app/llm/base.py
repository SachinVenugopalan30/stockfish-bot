from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

@dataclass
class TradeContext:
    ticker: str
    current_price: float
    trigger_type: str           # price | news | reddit
    trigger_detail: str
    position_quantity: float = 0.0     # 0 if no position
    position_avg_cost: float = 0.0     # 0 if no position
    cash_balance: float = 100000.0
    max_position_pct: float = 10.0
    portfolio_value: float = 100000.0
    recent_sentiment: float = 0.0      # -1.0 to 1.0
    created_at: datetime = field(default_factory=datetime.utcnow)

@dataclass
class Decision:
    action: str          # buy | sell | hold
    quantity: float      # number of shares
    reasoning: str       # one sentence
    confidence: float = 0.5    # 0.0 to 1.0

class LLMProvider(ABC):
    @abstractmethod
    async def decide(self, context: TradeContext) -> Decision:
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

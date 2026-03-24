import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class HeldPosition:
    ticker: str
    quantity: float
    avg_cost: float
    current_price: float   # 0.0 if price unavailable
    unrealized_pnl: float  # (current_price - avg_cost) * quantity


@dataclass
class TradeContext:
    ticker: str
    current_price: float
    trigger_type: str           # price | news | reddit
    trigger_detail: str
    position_quantity: float = 0.0
    position_avg_cost: float = 0.0
    cash_balance: float = 100000.0
    max_position_pct: float = 10.0
    portfolio_value: float = 100000.0
    recent_sentiment: float = 0.0
    signal_sentiment: Optional[float] = None  # FinBERT score of the specific triggering signal
    wallet_remaining: float = 50000.0   # wallet cap minus currently deployed capital
    portfolio_positions: List[HeldPosition] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    signal_strength: Optional[float] = None         # from SignalScorer (Step 3); None if scoring disabled
    normalized_features: Optional[dict] = None      # NormalizedFeatures as dict
    calibration_summary: Optional[str] = None       # from CalibrationTracker (Step 5)


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

    async def single_shot(self, system: str, user_message: str) -> str:
        """Single LLM call with no tools. Returns the raw text/JSON response."""
        raise NotImplementedError(f"{self.__class__.__name__} does not implement single_shot()")

    async def decide_with_tools(self, context: TradeContext, tool_executor) -> tuple[Decision, list[dict]]:
        """
        Agentic decision loop. Providers that support tool calling override this.
        Default falls back to decide() with an empty tool call trace.
        """
        decision = await self.decide(context)
        return decision, []

    @property
    def supports_tools(self) -> bool:
        return False

    def _parse_decision(self, raw: dict) -> Decision:
        """Validate raw LLM dict output against Pydantic schema and return Decision."""
        from app.llm.schemas import TradeDecisionSchema
        parsed = TradeDecisionSchema.model_validate(raw)
        return Decision(
            action=parsed.action,
            quantity=float(parsed.quantity),
            reasoning=parsed.reasoning,
            confidence=parsed.confidence,
        )

    async def _decide_with_retry(self, call_fn, retries: int = 2) -> Decision:
        """Call call_fn() up to (retries+1) times, backing off on parse errors."""
        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                return await call_fn()
            except Exception as e:
                last_err = e
                if attempt < retries:
                    wait = 0.5 * (2 ** attempt)
                    logger.warning(
                        f"LLM parse error (attempt {attempt + 1}/{retries + 1}): {e} — retrying in {wait:.1f}s"
                    )
                    await asyncio.sleep(wait)
        raise last_err

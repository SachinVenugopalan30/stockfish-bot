from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class TriggerEvent:
    ticker: str
    trigger_type: str  # price | news | reddit
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PriceSpikeEvent(TriggerEvent):
    pct_change: float = 0.0
    window_min: int = 5
    trigger_type: str = "price"

    @property
    def trigger_detail(self) -> str:
        direction = "+" if self.pct_change >= 0 else ""
        return f"{direction}{self.pct_change:.1f}% in {self.window_min}min"


@dataclass
class NewsEvent(TriggerEvent):
    headline: str = ""
    source: str = ""
    url: Optional[str] = None
    sentiment_score: Optional[float] = None   # FinBERT score attached by news monitor
    trigger_type: str = "news"

    @property
    def trigger_detail(self) -> str:
        return self.headline


@dataclass
class SentimentEvent(TriggerEvent):
    score: float = 0.0
    source: str = "reddit"
    post_title: str = ""
    trigger_type: str = "reddit"

    @property
    def trigger_detail(self) -> str:
        return self.post_title


@dataclass
class CompositeSignal(TriggerEvent):
    """Aggregated event produced by SignalAggregator from multiple raw signals."""
    events: List[TriggerEvent] = field(default_factory=list)
    agreement_score: float = 0.0        # -1.0 to +1.0 weighted direction
    dominant_direction: str = "mixed"   # bullish | bearish | mixed
    trigger_type: str = "composite"

    @property
    def trigger_detail(self) -> str:
        n = len(self.events)
        # Preserve insertion order, deduplicate
        seen = set()
        types_ordered = []
        for e in self.events:
            if e.trigger_type not in seen:
                seen.add(e.trigger_type)
                types_ordered.append(e.trigger_type)
        types = ", ".join(types_ordered)
        return f"{n} signals ({types}): {self.dominant_direction}"

    @property
    def sentiment_score(self) -> Optional[float]:
        """Return agreement_score as a proxy sentiment score."""
        return self.agreement_score if self.agreement_score != 0.0 else None

    @property
    def composite_score(self) -> float:
        """Alias for agreement_score for backwards compatibility."""
        return self.agreement_score

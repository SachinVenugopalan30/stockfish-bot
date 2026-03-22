from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class TriggerEvent:
    ticker: str
    trigger_type: str  # price | news | reddit
    created_at: datetime = field(default_factory=datetime.utcnow)

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

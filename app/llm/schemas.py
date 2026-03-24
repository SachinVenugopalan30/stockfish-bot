from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TradeDecisionSchema(BaseModel):
    action: Literal["buy", "sell", "hold"]
    quantity: int
    reasoning: str
    confidence: float

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    @field_validator("quantity")
    @classmethod
    def non_negative(cls, v: int) -> int:
        return max(0, int(v))


class ResearchReportSchema(BaseModel):
    price_summary: str
    technical_summary: str
    sentiment_summary: str
    news_summary: str
    web_search_summary: str = ""
    overall_assessment: str


class RiskAssessmentSchema(BaseModel):
    risk_level: Literal["low", "medium", "high"]
    suggested_position_pct: float = Field(ge=0.0, le=100.0)
    volatility_note: str
    portfolio_exposure_note: str
    recommendation: str

    @field_validator("suggested_position_pct")
    @classmethod
    def clamp_pct(cls, v: float) -> float:
        return max(0.0, min(100.0, float(v)))

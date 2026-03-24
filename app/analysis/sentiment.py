"""
SentimentAnalyzer — FinBERT-powered financial sentiment analysis.

Uses ProsusAI/FinBERT (HuggingFace transformers) when available,
with a graceful fallback to a keyword-counting heuristic.

FinBERT is domain-specific for financial text and returns three classes:
  positive, negative, neutral — mapped to a continuous score (-1 to +1).

Inference runs in a thread executor (transformers is synchronous).
The model is loaded once at startup and cached as a singleton.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_POSITIVE_WORDS = ["bullish", "buy", "moon", "surge", "beat", "profit", "gain", "up", "high", "great", "strong", "growth", "rally", "rise"]
_NEGATIVE_WORDS = ["bearish", "sell", "crash", "drop", "miss", "loss", "down", "low", "bad", "short", "weak", "decline", "fall", "slump"]


@dataclass
class SentimentResult:
    score: float          # -1.0 (very negative) to +1.0 (very positive)
    label: str            # positive | negative | neutral
    positive: float       # probability 0–1
    negative: float
    neutral: float
    model: str            # finbert | keyword


class SentimentAnalyzer:
    """
    Wraps FinBERT for async financial sentiment analysis.
    Falls back to keyword counting if transformers is unavailable.
    """

    def __init__(self) -> None:
        self._pipeline = None
        self._model_name = "ProsusAI/finbert"
        self._loaded = False
        self._use_fallback = False

    def load(self) -> None:
        """
        Load FinBERT synchronously (called once at startup in a thread executor).
        Safe to call multiple times — only loads once.
        """
        if self._loaded:
            return
        try:
            from transformers import pipeline
            logger.info(f"Loading FinBERT model: {self._model_name}")
            self._pipeline = pipeline(
                "text-classification",
                model=self._model_name,
                top_k=None,         # return all 3 class scores
                truncation=True,
                max_length=512,
            )
            self._loaded = True
            logger.info("FinBERT model loaded successfully")
        except Exception as e:
            logger.warning(
                f"FinBERT unavailable ({e}) — falling back to keyword sentiment"
            )
            self._use_fallback = True
            self._loaded = True

    async def analyze(self, text: str) -> SentimentResult:
        """
        Analyze text and return a SentimentResult.
        Runs FinBERT inference in a thread executor to stay async-safe.
        """
        if not self._loaded:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.load)

        if self._use_fallback or self._pipeline is None:
            return self._keyword_sentiment(text)

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._run_inference, text)
            return result
        except Exception as e:
            logger.warning(f"FinBERT inference error: {e} — using keyword fallback")
            return self._keyword_sentiment(text)

    def _run_inference(self, text: str) -> SentimentResult:
        """Synchronous FinBERT inference — runs in executor."""
        outputs = self._pipeline(text)
        # outputs is a list of lists: [[{label, score}, ...]]
        scores = {item["label"].lower(): item["score"] for item in outputs[0]}
        pos = scores.get("positive", 0.0)
        neg = scores.get("negative", 0.0)
        neu = scores.get("neutral", 0.0)

        # Map to continuous score: positive drives +1, negative drives -1
        score = pos - neg

        if pos >= neg and pos >= neu:
            label = "positive"
        elif neg >= pos and neg >= neu:
            label = "negative"
        else:
            label = "neutral"

        result = SentimentResult(
            score=round(score, 4),
            label=label,
            positive=round(pos, 4),
            negative=round(neg, 4),
            neutral=round(neu, 4),
            model="finbert",
        )
        logger.debug(
            f"FinBERT: score={result.score:+.4f} ({result.label}) "
            f"pos={result.positive:.3f} neg={result.negative:.3f} neu={result.neutral:.3f} | {text[:120]}"
        )
        return result

    def _keyword_sentiment(self, text: str) -> SentimentResult:
        """Simple keyword fallback when FinBERT is unavailable."""
        text_lower = text.lower()
        pos_count = sum(1 for w in _POSITIVE_WORDS if w in text_lower)
        neg_count = sum(1 for w in _NEGATIVE_WORDS if w in text_lower)
        total = pos_count + neg_count

        if total == 0:
            return SentimentResult(score=0.0, label="neutral", positive=0.33, negative=0.33, neutral=0.34, model="keyword")

        score = (pos_count - neg_count) / total
        if score > 0.1:
            label, pos, neg, neu = "positive", 0.6, 0.2, 0.2
        elif score < -0.1:
            label, pos, neg, neu = "negative", 0.2, 0.6, 0.2
        else:
            label, pos, neg, neu = "neutral", 0.25, 0.25, 0.5

        return SentimentResult(
            score=round(score, 4),
            label=label,
            positive=pos,
            negative=neg,
            neutral=neu,
            model="keyword",
        )


# Module-level singleton — initialized in main.py
_analyzer: Optional[SentimentAnalyzer] = None


def get_analyzer() -> SentimentAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = SentimentAnalyzer()
    return _analyzer

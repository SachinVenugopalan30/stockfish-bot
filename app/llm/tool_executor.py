"""
ToolExecutor — dispatches agent tool calls to the appropriate data sources.

Each tool opens its own DB session so a failed query cannot abort the
caller's transaction.  Results are JSON-serializable strings.
"""
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import desc, select

from app.analysis.service import TechnicalAnalysisService
from app.database import async_session_factory
from app.engine.portfolio import PortfolioManager
from app.models.market_data import NewsEvent as NewsEventModel
from app.models.market_data import PriceCache, PriceHistory, SentimentScore

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 15  # safety limit — research agent alone needs 7+ calls


class ToolExecutor:
    def __init__(
        self,
        portfolio: PortfolioManager,
        ta_service: TechnicalAnalysisService,
    ) -> None:
        self.portfolio = portfolio
        self.ta_service = ta_service
        self.call_count = 0

    async def execute(self, tool_name: str, arguments: dict) -> str:
        """Dispatch a tool call and return a JSON string result."""
        self.call_count += 1
        if self.call_count > MAX_TOOL_CALLS:
            return json.dumps({"error": "Tool call limit reached. Please submit your decision now."})

        try:
            if tool_name == "get_price_history":
                return await self._get_price_history(**arguments)
            elif tool_name == "get_technical_indicators":
                return await self._get_technical_indicators(**arguments)
            elif tool_name == "get_sentiment":
                return await self._get_sentiment(**arguments)
            elif tool_name == "get_recent_news":
                return await self._get_recent_news(**arguments)
            elif tool_name == "check_portfolio":
                return await self._check_portfolio()
            elif tool_name == "web_search":
                return await self._web_search(**arguments)
            elif tool_name in ("submit_decision", "submit_research_report"):
                # Terminal tools — handled by the caller, not dispatched here
                return json.dumps({"status": "ok"})
            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as e:
            logger.warning(f"Tool '{tool_name}' failed: {e}")
            return json.dumps({"error": str(e)})

    async def _get_price_history(self, ticker: str, hours: int = 24) -> str:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        async with async_session_factory() as session:
            result = await session.execute(
                select(PriceHistory.price, PriceHistory.recorded_at)
                .where(PriceHistory.ticker == ticker.upper(), PriceHistory.recorded_at >= cutoff)
                .order_by(desc(PriceHistory.recorded_at))
                .limit(50)
            )
            rows = result.all()
        if not rows:
            return json.dumps({"ticker": ticker, "error": "No price history available"})

        prices = [{"price": float(r.price), "time": r.recorded_at.isoformat()} for r in reversed(rows)]
        first_price = prices[0]["price"]
        last_price = prices[-1]["price"]
        change_pct = ((last_price - first_price) / first_price * 100) if first_price > 0 else 0

        return json.dumps({
            "ticker": ticker,
            "period_hours": hours,
            "data_points": len(prices),
            "current_price": last_price,
            "price_change_pct": round(change_pct, 2),
            "high": round(max(p["price"] for p in prices), 2),
            "low": round(min(p["price"] for p in prices), 2),
            "prices": prices[-10:],
        })

    async def _get_technical_indicators(self, ticker: str) -> str:
        async with async_session_factory() as session:
            indicators = await self.ta_service.get_latest_indicators(ticker.upper(), session)
        if not indicators:
            return json.dumps({
                "ticker": ticker,
                "error": "No technical indicators computed yet — insufficient price history",
            })
        return json.dumps({"ticker": ticker, "indicators": indicators})

    async def _get_sentiment(self, ticker: str, limit: int = 5) -> str:
        async with async_session_factory() as session:
            result = await session.execute(
                select(SentimentScore)
                .where(SentimentScore.ticker == ticker.upper())
                .order_by(desc(SentimentScore.recorded_at))
                .limit(limit)
            )
            rows = result.scalars().all()
        if not rows:
            return json.dumps({"ticker": ticker, "error": "No sentiment data available"})

        scores = [
            {
                "score": float(r.score),
                "source": r.source,
                "model": getattr(r, "model", None) or "keyword",
                "positive": float(r.positive_score) if getattr(r, "positive_score", None) else None,
                "negative": float(r.negative_score) if getattr(r, "negative_score", None) else None,
                "neutral": float(r.neutral_score) if getattr(r, "neutral_score", None) else None,
                "recorded_at": r.recorded_at.isoformat(),
            }
            for r in rows
        ]
        avg_score = sum(s["score"] for s in scores) / len(scores)
        return json.dumps({
            "ticker": ticker,
            "average_score": round(avg_score, 4),
            "interpretation": "positive" if avg_score > 0.1 else "negative" if avg_score < -0.1 else "neutral",
            "recent_scores": scores,
        })

    async def _get_recent_news(self, ticker: str, limit: int = 5) -> str:
        async with async_session_factory() as session:
            result = await session.execute(
                select(NewsEventModel)
                .where(NewsEventModel.ticker == ticker.upper())
                .order_by(desc(NewsEventModel.created_at))
                .limit(limit)
            )
            rows = result.scalars().all()
        if not rows:
            return json.dumps({"ticker": ticker, "error": "No recent news found"})

        headlines = [
            {
                "headline": r.headline,
                "source": r.source,
                "url": r.url,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
        return json.dumps({"ticker": ticker, "headlines": headlines})

    async def _web_search(self, query: str) -> str:
        try:
            import asyncio

            from duckduckgo_search import DDGS
            # DDGS.text() is synchronous — run in executor to avoid blocking the event loop
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                None,
                lambda: list(DDGS().text(query, max_results=5)),
            )
            if not results:
                return json.dumps({"query": query, "results": [], "note": "No results found"})
            formatted = [
                {"title": r.get("title", ""), "snippet": r.get("body", ""), "url": r.get("href", "")}
                for r in results
            ]
            return json.dumps({"query": query, "results": formatted})
        except ImportError:
            return json.dumps({"error": "duckduckgo-search not installed"})
        except Exception as e:
            logger.warning(f"Web search failed: {e}")
            return json.dumps({"error": f"Web search unavailable: {e}"})

    async def _check_portfolio(self) -> str:
        async with async_session_factory() as session:
            positions_out = []
            for ticker, pos in self.portfolio.positions.items():
                price_result = await session.execute(
                    select(PriceCache.price).where(PriceCache.ticker == ticker)
                )
                price = float(price_result.scalar() or pos["avg_cost"])
                unrealized = pos["quantity"] * (price - pos["avg_cost"])
                positions_out.append({
                    "ticker": ticker,
                    "quantity": pos["quantity"],
                    "avg_cost": round(pos["avg_cost"], 2),
                    "current_price": round(price, 2),
                    "current_value": round(pos["quantity"] * price, 2),
                    "unrealized_pnl": round(unrealized, 2),
                })

            portfolio_value = await self.portfolio.get_portfolio_value(session)

        return json.dumps({
            "cash_balance": round(self.portfolio.cash, 2),
            "invested_capital": round(self.portfolio.invested_capital, 2),
            "portfolio_value": round(portfolio_value, 2),
            "wallet_cap": round(self.portfolio.effective_wallet, 2),
            "positions": positions_out,
        })

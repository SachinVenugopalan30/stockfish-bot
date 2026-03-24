"""
TechnicalAnalysisService — computes and persists technical indicators for all tracked tickers.
Scheduled every 5 minutes by APScheduler, and callable on-demand by the agentic tool executor.
"""
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.indicators import (
    compute_bollinger,
    compute_ema,
    compute_macd,
    compute_rsi,
    compute_sma,
    macd_signal,
    rsi_signal,
)
from app.database import async_session_factory
from app.models.market_data import PriceHistory, TechnicalIndicator, TickerMetadata

logger = logging.getLogger(__name__)

# How many price ticks to pull when computing indicators
_PRICE_HISTORY_LIMIT = 200


class TechnicalAnalysisService:
    async def compute_all(self) -> None:
        """Recompute indicators for every tracked ticker. Called by APScheduler."""
        async with async_session_factory() as session:
            tickers_result = await session.execute(select(TickerMetadata.ticker))
            tickers = [row[0] for row in tickers_result.fetchall()]

        for ticker in tickers:
            try:
                async with async_session_factory() as session:
                    await self.compute_for_ticker(ticker, session)
            except Exception as e:
                logger.warning(f"Indicator compute failed for {ticker}: {e}")

    async def compute_for_ticker(self, ticker: str, session: AsyncSession) -> dict:
        """
        Fetch recent prices, compute RSI/MACD/Bollinger/SMA/EMA,
        persist to TechnicalIndicator, and return a summary dict.
        """
        prices = await self._fetch_prices(ticker, session)
        if len(prices) < 15:
            logger.debug(f"Insufficient price history for {ticker}: {len(prices)} ticks")
            return {}

        now = datetime.utcnow()
        rows = []
        summary = {}

        # RSI
        rsi_val = compute_rsi(prices)
        if rsi_val is not None:
            sig = rsi_signal(rsi_val)
            rows.append(TechnicalIndicator(
                ticker=ticker, indicator_type="RSI",
                value=Decimal(str(round(rsi_val, 4))),
                signal=sig, computed_at=now,
            ))
            summary["rsi"] = {"value": round(rsi_val, 2), "signal": sig}

        # MACD
        macd = compute_macd(prices)
        if macd:
            sig = macd_signal(macd["histogram"])
            rows.append(TechnicalIndicator(
                ticker=ticker, indicator_type="MACD",
                value=Decimal(str(macd["macd"])),
                signal=sig, computed_at=now,
            ))
            summary["macd"] = {**macd, "signal": sig}

        # Bollinger Bands
        bb = compute_bollinger(prices)
        if bb:
            # Signal: price above upper = overbought, below lower = oversold
            last_price = prices[-1]
            if last_price > bb["upper"]:
                bb_sig = "overbought"
            elif last_price < bb["lower"]:
                bb_sig = "oversold"
            else:
                bb_sig = "neutral"
            rows.append(TechnicalIndicator(
                ticker=ticker, indicator_type="BOLLINGER",
                value=Decimal(str(bb["middle"])),
                signal=bb_sig, computed_at=now,
            ))
            summary["bollinger"] = {**bb, "signal": bb_sig}

        # SMA 20
        sma = compute_sma(prices, 20)
        if sma is not None:
            rows.append(TechnicalIndicator(
                ticker=ticker, indicator_type="SMA_20",
                value=Decimal(str(round(sma, 4))),
                signal="neutral", computed_at=now,
            ))
            summary["sma_20"] = round(sma, 2)

        # EMA 12
        ema = compute_ema(prices, 12)
        if ema is not None:
            rows.append(TechnicalIndicator(
                ticker=ticker, indicator_type="EMA_12",
                value=Decimal(str(round(ema, 4))),
                signal="neutral", computed_at=now,
            ))
            summary["ema_12"] = round(ema, 2)

        if rows:
            session.add_all(rows)
            await session.flush()

        return summary

    async def get_latest_indicators(self, ticker: str, session: AsyncSession) -> dict:
        """Return the most recently computed indicator values for a ticker."""
        indicator_types = ["RSI", "MACD", "BOLLINGER", "SMA_20", "EMA_12"]
        result = {}
        for itype in indicator_types:
            row = await session.execute(
                select(TechnicalIndicator)
                .where(
                    TechnicalIndicator.ticker == ticker,
                    TechnicalIndicator.indicator_type == itype,
                )
                .order_by(desc(TechnicalIndicator.computed_at))
                .limit(1)
            )
            indicator = row.scalar_one_or_none()
            if indicator:
                result[itype.lower()] = {
                    "value": float(indicator.value),
                    "signal": indicator.signal,
                    "computed_at": indicator.computed_at.isoformat(),
                }
        return result

    async def _fetch_prices(self, ticker: str, session: AsyncSession) -> list[float]:
        result = await session.execute(
            select(PriceHistory.price)
            .where(PriceHistory.ticker == ticker)
            .order_by(desc(PriceHistory.recorded_at))
            .limit(_PRICE_HISTORY_LIMIT)
        )
        rows = result.scalars().all()
        return [float(p) for p in reversed(rows)]  # chronological order

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.analysis.sentiment import get_analyzer
from app.analysis.service import TechnicalAnalysisService
from app.api.routes import router, set_bot_state
from app.api.websocket import manager as ws_manager
from app.config import load_config
from app.database import async_session_factory, engine
from app.engine.calibration import CalibrationTracker
from app.engine.decision import DecisionEngine
from app.engine.portfolio import PortfolioManager
from app.llm.factory import get_provider
from app.models import Base
from app.monitors.news import NewsMonitor
from app.monitors.price import PriceMonitor
from app.monitors.reddit import RedditMonitor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Module-level references so lifespan can clean up
_monitors = []
_decision_engine: DecisionEngine | None = None
_scheduler: AsyncIOScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _monitors, _decision_engine, _scheduler

    # 1. Load config
    settings = load_config()
    logger.info(f"Config loaded. LLM: {settings.llm.provider}/{settings.llm.model}")

    # 2. Create DB tables (Alembic handles migrations; this is a fallback for tests)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 3. Load ticker metadata (seed if empty)
    await seed_ticker_metadata(settings)

    # 4. Build LLM provider
    llm = get_provider(
        provider=settings.llm.provider,
        model=settings.llm.model,
        ollama_host=settings.llm.ollama_host,
    )

    # 5. Portfolio manager
    portfolio = PortfolioManager(settings)
    async with async_session_factory() as session:
        await portfolio.load_from_db(session)

    # 6. Decision engine (ta_service injected after scheduler setup below)
    _decision_engine = DecisionEngine(settings, llm, portfolio)
    _decision_engine.set_broadcast_callback(ws_manager.broadcast)
    await _decision_engine.start()

    # 7. Start monitors
    event_callback = _decision_engine.push_event
    _monitors = [
        PriceMonitor(settings, event_callback),
        NewsMonitor(settings, event_callback),
        RedditMonitor(settings, event_callback),
    ]
    for monitor in _monitors:
        await monitor.start()

    # 8. Scheduler: hourly snapshots + 5-min indicator recompute
    _ta_service = TechnicalAnalysisService()
    _decision_engine.ta_service = _ta_service  # inject so ToolExecutor can use it
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _decision_engine.take_portfolio_snapshot,
        trigger="interval",
        hours=1,
        id="portfolio_snapshot",
    )
    _scheduler.add_job(
        _ta_service.compute_all,
        trigger="interval",
        minutes=5,
        id="technical_indicators",
    )
    if settings.signal.calibration_enabled:
        calibration_tracker = CalibrationTracker()
        _decision_engine.set_calibration_tracker(calibration_tracker)
        _scheduler.add_job(
            calibration_tracker.evaluate_pending_job,
            trigger="interval",
            minutes=30,
            id="calibration_eval",
        )
    _scheduler.start()

    # Compute indicators immediately on startup
    asyncio.create_task(_ta_service.compute_all())

    # Preload FinBERT in background (non-blocking)
    analyzer = get_analyzer()
    asyncio.get_event_loop().run_in_executor(None, analyzer.load)

    # Take an immediate snapshot on startup so the chart has data from day one
    await _decision_engine.take_portfolio_snapshot()

    # 9. Expose state to routes
    set_bot_state({
        "running": True,
        "llm_provider": settings.llm.provider,
        "llm_model": settings.llm.model,
        "last_decision_at": None,
        "start_time": datetime.utcnow(),
        "portfolio": portfolio,
        "ta_service": _ta_service,
    })

    logger.info("Stockfish API started")
    yield

    # Shutdown
    set_bot_state({"running": False})
    if _scheduler:
        _scheduler.shutdown(wait=False)
    if _decision_engine:
        await _decision_engine.stop()
    for monitor in _monitors:
        await monitor.stop()
    await engine.dispose()
    logger.info("Stockfish API shutdown complete")


async def seed_ticker_metadata(settings) -> None:
    """Seed a minimal set of tickers if the table is empty."""
    from sqlalchemy import func, select

    from app.models import TickerMetadata

    async with async_session_factory() as session:
        count = await session.execute(select(func.count(TickerMetadata.ticker)))
        if count.scalar() > 0:
            return

        default_tickers = [
            TickerMetadata(ticker="AAPL", company_name="Apple Inc", sector="Technology", market_cap_tier="large"),
            TickerMetadata(ticker="NVDA", company_name="NVIDIA Corporation", sector="Technology", market_cap_tier="large"),
            TickerMetadata(ticker="TSLA", company_name="Tesla Inc", sector="Consumer Discretionary", market_cap_tier="large"),
            TickerMetadata(ticker="MSFT", company_name="Microsoft Corporation", sector="Technology", market_cap_tier="large"),
            TickerMetadata(ticker="AMZN", company_name="Amazon", sector="Consumer Discretionary", market_cap_tier="large"),
            TickerMetadata(ticker="GOOGL", company_name="Alphabet Inc", sector="Technology", market_cap_tier="large"),
            TickerMetadata(ticker="META", company_name="Meta Platforms", sector="Technology", market_cap_tier="large"),
            TickerMetadata(ticker="NFLX", company_name="Netflix", sector="Communication Services", market_cap_tier="large"),
            TickerMetadata(ticker="AMD", company_name="Advanced Micro Devices", sector="Technology", market_cap_tier="large"),
            TickerMetadata(ticker="SPY", company_name="SPDR S&P 500 ETF", sector="ETF", market_cap_tier="large"),
        ]
        session.add_all(default_tickers)
        await session.commit()
        logger.info(f"Seeded {len(default_tickers)} default tickers")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Stockfish API",
        description="Paper trading bot with real-time news, price, and Reddit monitoring",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    return app


app = create_app()

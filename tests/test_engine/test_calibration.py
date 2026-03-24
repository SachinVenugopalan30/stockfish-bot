"""Tests for CalibrationTracker (Step 5 — Post-Decision Calibration)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import PortfolioConfig, Settings, SignalConfig, TriggersConfig
from app.engine.calibration import CalibrationTracker
from app.engine.decision import DecisionEngine
from app.engine.events import PriceSpikeEvent
from app.engine.portfolio import PortfolioManager
from app.models import Base, DecisionOutcome, PriceCache, TickerMetadata, Trade
from tests.conftest import MockLLMProvider

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def session(db_engine) -> AsyncSession:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        yield s


@pytest_asyncio.fixture
async def test_db(db_engine):
    """Return a session factory (for DecisionEngine tests)."""
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def tracker() -> CalibrationTracker:
    return CalibrationTracker()


@pytest.fixture
def settings_calibration() -> Settings:
    return Settings(
        signal=SignalConfig(
            calibration_enabled=True,
            calibration_lookback_days=30,
            confidence_gate=0.0,
        ),
        portfolio=PortfolioConfig(starting_cash=100_000.0, max_position_pct=10.0),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trade(session: AsyncSession, ticker: str = "AAPL", action: str = "buy",
                price: float = 150.0, confidence: float = 0.8,
                created_at: datetime | None = None) -> Trade:
    """Create a Trade and add to session (does not flush/commit)."""
    trade = Trade(
        ticker=ticker,
        action=action,
        quantity=Decimal("10"),
        price_at_exec=Decimal(str(price)),
        entry_price=Decimal(str(price)),
        reasoning="test",
        trigger_type="price",
        trigger_detail="+3%",
        llm_provider="mock",
        confidence=Decimal(str(round(confidence, 3))),
        created_at=created_at or datetime.now(timezone.utc),
    )
    session.add(trade)
    return trade


# ---------------------------------------------------------------------------
# Test: record_decision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_decision_creates_row(session: AsyncSession, tracker: CalibrationTracker) -> None:
    """record_decision should create a DecisionOutcome with correct fields."""
    trade = _make_trade(session, ticker="AAPL", action="buy", price=100.0, confidence=0.75)
    await session.flush()  # assign trade.id

    await tracker.record_decision(trade, signal_strength=0.65, session=session)
    await session.commit()

    result = await session.execute(select(DecisionOutcome).where(DecisionOutcome.trade_id == trade.id))
    outcome = result.scalar_one()

    assert outcome.ticker == "AAPL"
    assert outcome.action == "buy"
    assert float(outcome.confidence) == pytest.approx(0.75, abs=0.001)
    assert float(outcome.price_at_decision) == pytest.approx(100.0, abs=0.001)
    assert float(outcome.signal_strength) == pytest.approx(0.65, abs=0.001)
    assert outcome.price_at_1h is None
    assert outcome.price_at_24h is None
    assert outcome.pct_change_1h is None
    assert outcome.pct_change_24h is None
    assert outcome.outcome_correct_1h is None
    assert outcome.outcome_correct_24h is None
    assert outcome.evaluated_at is None


@pytest.mark.asyncio
async def test_record_decision_none_signal_strength(session: AsyncSession, tracker: CalibrationTracker) -> None:
    """signal_strength=None should store None in the DB."""
    trade = _make_trade(session)
    await session.flush()

    await tracker.record_decision(trade, signal_strength=None, session=session)
    await session.commit()

    result = await session.execute(select(DecisionOutcome))
    outcome = result.scalar_one()
    assert outcome.signal_strength is None


# ---------------------------------------------------------------------------
# Test: evaluate_pending — skips recent rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_pending_skips_recent(session: AsyncSession, tracker: CalibrationTracker) -> None:
    """Rows decided < 1h ago should NOT have price_at_1h filled."""
    now = datetime.now(timezone.utc)
    trade = _make_trade(session, created_at=now - timedelta(minutes=30))
    await session.flush()
    await tracker.record_decision(trade, signal_strength=0.5, session=session)
    await session.commit()

    # Seed price cache
    session.add(PriceCache(ticker="AAPL", price=Decimal("160.0")))
    await session.commit()

    await tracker.evaluate_pending(session)

    result = await session.execute(select(DecisionOutcome))
    outcome = result.scalar_one()
    assert outcome.price_at_1h is None
    assert outcome.price_at_24h is None
    assert outcome.evaluated_at is None


# ---------------------------------------------------------------------------
# Test: evaluate_pending — fills 1h data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_pending_fills_1h(session: AsyncSession, tracker: CalibrationTracker) -> None:
    """Row decided > 1h ago should have price_at_1h filled."""
    now = datetime.now(timezone.utc)
    trade = _make_trade(session, ticker="AAPL", action="buy", price=100.0,
                        created_at=now - timedelta(hours=2))
    await session.flush()
    await tracker.record_decision(trade, signal_strength=0.5, session=session)
    await session.commit()

    session.add(PriceCache(ticker="AAPL", price=Decimal("105.0")))
    await session.commit()

    await tracker.evaluate_pending(session)

    result = await session.execute(select(DecisionOutcome))
    outcome = result.scalar_one()

    assert outcome.price_at_1h is not None
    assert float(outcome.price_at_1h) == pytest.approx(105.0, abs=0.01)
    # pct_change = (105 - 100) / 100 * 100 = +5%
    assert float(outcome.pct_change_1h) == pytest.approx(5.0, abs=0.01)
    # buy + positive pct = correct
    assert outcome.outcome_correct_1h is True
    # 24h not yet due
    assert outcome.price_at_24h is None
    assert outcome.evaluated_at is None


# ---------------------------------------------------------------------------
# Test: evaluate_pending — fills 24h data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_pending_fills_24h(session: AsyncSession, tracker: CalibrationTracker) -> None:
    """Row decided > 24h ago should have both 1h and 24h filled, evaluated_at set."""
    now = datetime.now(timezone.utc)
    trade = _make_trade(session, ticker="TSLA", action="sell", price=200.0,
                        created_at=now - timedelta(hours=25))
    await session.flush()
    await tracker.record_decision(trade, signal_strength=0.7, session=session)
    await session.commit()

    session.add(PriceCache(ticker="TSLA", price=Decimal("190.0")))
    await session.commit()

    await tracker.evaluate_pending(session)

    result = await session.execute(select(DecisionOutcome))
    outcome = result.scalar_one()

    # sell + price dropped = correct
    assert outcome.price_at_24h is not None
    assert float(outcome.price_at_24h) == pytest.approx(190.0, abs=0.01)
    # pct = (190 - 200) / 200 * 100 = -5%
    assert float(outcome.pct_change_24h) == pytest.approx(-5.0, abs=0.01)
    assert outcome.outcome_correct_24h is True
    # evaluated_at should be set
    assert outcome.evaluated_at is not None


# ---------------------------------------------------------------------------
# Test: evaluate_pending — hold correctness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_pending_hold_correctness(session: AsyncSession, tracker: CalibrationTracker) -> None:
    """Hold is correct when abs(pct_change) <= 1.0."""
    now = datetime.now(timezone.utc)
    trade = _make_trade(session, ticker="MSFT", action="hold", price=300.0,
                        created_at=now - timedelta(hours=2))
    await session.flush()
    await tracker.record_decision(trade, signal_strength=0.3, session=session)
    await session.commit()

    # Price barely moved: 300.5 → 0.17% change (within 1%)
    session.add(PriceCache(ticker="MSFT", price=Decimal("300.5")))
    await session.commit()

    await tracker.evaluate_pending(session)

    result = await session.execute(select(DecisionOutcome))
    outcome = result.scalar_one()
    assert outcome.outcome_correct_1h is True


@pytest.mark.asyncio
async def test_evaluate_pending_hold_incorrect_large_move(session: AsyncSession, tracker: CalibrationTracker) -> None:
    """Hold is incorrect when abs(pct_change) > 1.0."""
    now = datetime.now(timezone.utc)
    trade = _make_trade(session, ticker="NVDA", action="hold", price=500.0,
                        created_at=now - timedelta(hours=2))
    await session.flush()
    await tracker.record_decision(trade, signal_strength=0.3, session=session)
    await session.commit()

    # Large move: +3%
    session.add(PriceCache(ticker="NVDA", price=Decimal("515.0")))
    await session.commit()

    await tracker.evaluate_pending(session)

    result = await session.execute(select(DecisionOutcome))
    outcome = result.scalar_one()
    assert outcome.outcome_correct_1h is False


# ---------------------------------------------------------------------------
# Test: evaluate_pending — missing price cache handled gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_pending_missing_price_cache(session: AsyncSession, tracker: CalibrationTracker) -> None:
    """Ticker not in PriceCache → price_at_1h stays None, no exception raised."""
    now = datetime.now(timezone.utc)
    trade = _make_trade(session, ticker="DELIST", action="buy", price=50.0,
                        created_at=now - timedelta(hours=2))
    await session.flush()
    await tracker.record_decision(trade, signal_strength=0.5, session=session)
    await session.commit()

    # Do NOT seed price cache for DELIST

    await tracker.evaluate_pending(session)  # should not raise

    result = await session.execute(select(DecisionOutcome))
    outcome = result.scalar_one()
    assert outcome.price_at_1h is None


# ---------------------------------------------------------------------------
# Test: get_calibration_summary — empty when no evaluated rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_calibration_summary_empty(session: AsyncSession, tracker: CalibrationTracker) -> None:
    """Returns empty string when no evaluated outcomes exist."""
    summary = await tracker.get_calibration_summary(session, lookback_days=30)
    assert summary == ""


@pytest.mark.asyncio
async def test_get_calibration_summary_unevaluated_only(session: AsyncSession, tracker: CalibrationTracker) -> None:
    """Returns empty string when outcomes exist but none are evaluated (outcome_correct_1h IS NULL)."""
    now = datetime.now(timezone.utc)
    trade = _make_trade(session, created_at=now - timedelta(minutes=30))
    await session.flush()
    await tracker.record_decision(trade, signal_strength=0.5, session=session)
    await session.commit()

    summary = await tracker.get_calibration_summary(session, lookback_days=30)
    assert summary == ""


# ---------------------------------------------------------------------------
# Test: get_calibration_summary — correct stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_calibration_summary_stats(session: AsyncSession, tracker: CalibrationTracker) -> None:
    """Summary contains correct accuracy counts and formatted string."""
    now = datetime.now(timezone.utc)

    # Seed 4 evaluated outcomes: 3 correct_1h, 2 correct_24h
    rows = [
        # (action, confidence, correct_1h, correct_24h)
        ("buy",  0.80, True,  True),
        ("buy",  0.75, True,  False),
        ("sell", 0.50, True,  True),
        ("hold", 0.30, False, None),
    ]

    for i, (action, conf, c1h, c24h) in enumerate(rows):
        # Insert directly to control outcome_correct fields
        outcome = DecisionOutcome(
            trade_id=i + 100,
            ticker="AAPL",
            action=action,
            confidence=Decimal(str(conf)),
            price_at_decision=Decimal("100.00"),
            signal_strength=Decimal("0.5"),
            decided_at=now - timedelta(days=1),
            price_at_1h=Decimal("102.00"),
            pct_change_1h=Decimal("2.00"),
            outcome_correct_1h=c1h,
            price_at_24h=Decimal("103.00") if c24h is not None else None,
            pct_change_24h=Decimal("3.00") if c24h is not None else None,
            outcome_correct_24h=c24h,
            evaluated_at=now - timedelta(hours=1),
        )
        session.add(outcome)
    await session.commit()

    summary = await tracker.get_calibration_summary(session, lookback_days=30)

    assert summary != ""
    assert "=== CALIBRATION" in summary
    assert "last 30 days" in summary
    assert "4 decisions" in summary
    # 3/4 correct 1h = 75%
    assert "1h=75%" in summary
    # By action: BUY 2/2=100%, SELL 1/1=100%, HOLD 0/1=0%
    assert "BUY 1h=100%" in summary
    assert "SELL 1h=100%" in summary
    assert "HOLD 1h=0%" in summary
    # By action must include 24h accuracy per action
    assert "24h=" in summary
    # Label must be "By confidence:" not "By conf:"
    assert "By confidence:" in summary


# ---------------------------------------------------------------------------
# Test: get_calibration_summary — confidence buckets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_calibration_summary_confidence_buckets(session: AsyncSession, tracker: CalibrationTracker) -> None:
    """Confidence bucket breakdown should be in the summary."""
    now = datetime.now(timezone.utc)

    # High confidence (>0.7): 1 correct, 1 wrong
    # Med confidence (0.4-0.7): 1 correct
    # Low confidence (<0.4): 1 wrong
    bucket_rows = [
        ("buy", 0.80, True),
        ("buy", 0.72, False),
        ("sell", 0.55, True),
        ("hold", 0.35, False),
    ]

    for i, (action, conf, c1h) in enumerate(bucket_rows):
        outcome = DecisionOutcome(
            trade_id=i + 200,
            ticker="TSLA",
            action=action,
            confidence=Decimal(str(conf)),
            price_at_decision=Decimal("200.00"),
            signal_strength=Decimal("0.5"),
            decided_at=now - timedelta(days=2),
            price_at_1h=Decimal("202.00"),
            pct_change_1h=Decimal("1.00"),
            outcome_correct_1h=c1h,
            evaluated_at=now - timedelta(hours=2),
        )
        session.add(outcome)
    await session.commit()

    summary = await tracker.get_calibration_summary(session, lookback_days=30)

    assert "High(>0.7)" in summary
    assert "Med(0.4-0.7)" in summary
    assert "Low(<0.4)" in summary


# ---------------------------------------------------------------------------
# Test: confidence gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confidence_gate_converts_buy_to_hold(test_db) -> None:
    """Buy decision below confidence_gate should be converted to hold."""
    settings = Settings(
        signal=SignalConfig(confidence_gate=0.5, calibration_enabled=False),
        portfolio=PortfolioConfig(starting_cash=100_000.0, max_position_pct=10.0),
        triggers=TriggersConfig(cooldown_min=0),
    )
    # Mock LLM returns buy with confidence 0.3 (below gate of 0.5)
    llm = MockLLMProvider(action="buy", quantity=5.0)
    # Override confidence to 0.3
    from app.llm.base import Decision
    original_decide = llm.decide

    async def low_confidence_decide(context):
        d = await original_decide(context)
        return Decision(action=d.action, quantity=d.quantity, reasoning=d.reasoning, confidence=0.3)

    llm.decide = low_confidence_decide

    portfolio = PortfolioManager(settings)
    engine = DecisionEngine(settings, llm, portfolio)

    async with test_db() as session:
        session.add(PriceCache(ticker="AAPL", price=Decimal("150.00")))
        session.add(TickerMetadata(ticker="AAPL", company_name="Apple", sector="Tech", market_cap_tier="large"))
        await session.commit()

    with patch("app.engine.decision.async_session_factory", test_db):
        event = PriceSpikeEvent(ticker="AAPL", pct_change=3.0, window_min=5)
        await engine._process_event(event)

    async with test_db() as session:
        result = await session.execute(select(Trade).where(Trade.ticker == "AAPL"))
        trade = result.scalar_one()
        # Confidence gate should have converted buy → hold
        assert trade.action == "hold"


@pytest.mark.asyncio
async def test_confidence_gate_allows_high_confidence(test_db) -> None:
    """Buy decision at or above confidence_gate should pass through."""
    settings = Settings(
        signal=SignalConfig(confidence_gate=0.5, calibration_enabled=False),
        portfolio=PortfolioConfig(starting_cash=100_000.0, max_position_pct=10.0),
        triggers=TriggersConfig(cooldown_min=0),
    )
    # MockLLMProvider returns confidence=0.8 by default (above gate of 0.5)
    llm = MockLLMProvider(action="buy", quantity=5.0)
    portfolio = PortfolioManager(settings)
    engine = DecisionEngine(settings, llm, portfolio)

    async with test_db() as session:
        session.add(PriceCache(ticker="NVDA", price=Decimal("400.00")))
        session.add(TickerMetadata(ticker="NVDA", company_name="Nvidia", sector="Tech", market_cap_tier="large"))
        await session.commit()

    with patch("app.engine.decision.async_session_factory", test_db):
        event = PriceSpikeEvent(ticker="NVDA", pct_change=3.0, window_min=5)
        await engine._process_event(event)

    async with test_db() as session:
        result = await session.execute(select(Trade).where(Trade.ticker == "NVDA"))
        trade = result.scalar_one()
        assert trade.action == "buy"


@pytest.mark.asyncio
async def test_confidence_gate_zero_disabled(test_db) -> None:
    """confidence_gate=0 (disabled) → buy passes through regardless of confidence."""
    settings = Settings(
        signal=SignalConfig(confidence_gate=0.0, calibration_enabled=False),
        portfolio=PortfolioConfig(starting_cash=100_000.0, max_position_pct=10.0),
        triggers=TriggersConfig(cooldown_min=0),
    )
    from app.llm.base import Decision

    llm = MockLLMProvider(action="buy", quantity=5.0)
    original_decide = llm.decide

    async def very_low_confidence_decide(context):
        d = await original_decide(context)
        return Decision(action=d.action, quantity=d.quantity, reasoning=d.reasoning, confidence=0.05)

    llm.decide = very_low_confidence_decide

    portfolio = PortfolioManager(settings)
    engine = DecisionEngine(settings, llm, portfolio)

    async with test_db() as session:
        session.add(PriceCache(ticker="MSFT", price=Decimal("300.00")))
        session.add(TickerMetadata(ticker="MSFT", company_name="Microsoft", sector="Tech", market_cap_tier="large"))
        await session.commit()

    with patch("app.engine.decision.async_session_factory", test_db):
        event = PriceSpikeEvent(ticker="MSFT", pct_change=3.0, window_min=5)
        await engine._process_event(event)

    async with test_db() as session:
        result = await session.execute(select(Trade).where(Trade.ticker == "MSFT"))
        trade = result.scalar_one()
        assert trade.action == "buy"


# ---------------------------------------------------------------------------
# Test: Integration — record_decision called after committed trade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_decision_called_on_committed_trade(test_db) -> None:
    """With calibration_enabled=True and tracker set, DecisionOutcome should be created."""
    settings = Settings(
        signal=SignalConfig(calibration_enabled=True, calibration_lookback_days=30, confidence_gate=0.0),
        portfolio=PortfolioConfig(starting_cash=100_000.0, max_position_pct=10.0),
        triggers=TriggersConfig(cooldown_min=0),
    )
    llm = MockLLMProvider(action="buy", quantity=5.0)
    portfolio = PortfolioManager(settings)
    engine = DecisionEngine(settings, llm, portfolio)

    calibration_tracker = CalibrationTracker()
    engine.set_calibration_tracker(calibration_tracker)

    async with test_db() as session:
        session.add(PriceCache(ticker="AAPL", price=Decimal("150.00")))
        session.add(TickerMetadata(ticker="AAPL", company_name="Apple", sector="Tech", market_cap_tier="large"))
        await session.commit()

    with patch("app.engine.decision.async_session_factory", test_db):
        event = PriceSpikeEvent(ticker="AAPL", pct_change=3.0, window_min=5)
        await engine._process_event(event)

    async with test_db() as session:
        # Verify a Trade was created
        trade_result = await session.execute(select(Trade).where(Trade.ticker == "AAPL"))
        trade = trade_result.scalar_one()
        assert trade.action == "buy"

        # Verify DecisionOutcome was created for that trade
        outcome_result = await session.execute(
            select(DecisionOutcome).where(DecisionOutcome.trade_id == trade.id)
        )
        outcome = outcome_result.scalar_one()
        assert outcome.ticker == "AAPL"
        assert outcome.action == "buy"
        assert outcome.price_at_1h is None  # not evaluated yet
        assert outcome.evaluated_at is None


# ---------------------------------------------------------------------------
# Test: pct_change == 0.0 edge case — breakeven buy treated as incorrect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_pending_zero_pct_change_buy_incorrect(
    session: AsyncSession, tracker: CalibrationTracker
) -> None:
    """Buy trade with 0% price change (breakeven) at 1h should be marked incorrect."""
    now = datetime.now(timezone.utc)
    decision_price = Decimal("150.00")

    trade = _make_trade(
        session,
        ticker="AAPL",
        action="buy",
        price=float(decision_price),
        created_at=now - timedelta(hours=2),
    )
    await session.flush()
    await tracker.record_decision(trade, signal_strength=0.5, session=session)
    await session.commit()

    # Mock PriceCache returns the exact same price — 0% change
    session.add(PriceCache(ticker="AAPL", price=decision_price))
    await session.commit()

    await tracker.evaluate_pending(session)

    result = await session.execute(select(DecisionOutcome))
    outcome = result.scalar_one()

    assert outcome.price_at_1h is not None
    assert float(outcome.pct_change_1h) == pytest.approx(0.0, abs=0.001)
    # Breakeven is incorrect for a buy
    assert outcome.outcome_correct_1h is False


# ---------------------------------------------------------------------------
# Test: confidence gate boundary — exactly at threshold is allowed through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confidence_gate_at_boundary_allows(test_db) -> None:
    """Buy with confidence == confidence_gate should NOT be converted to hold (strict less-than)."""
    gate = 0.5
    settings = Settings(
        signal=SignalConfig(confidence_gate=gate, calibration_enabled=False),
        portfolio=PortfolioConfig(starting_cash=100_000.0, max_position_pct=10.0),
        triggers=TriggersConfig(cooldown_min=0),
    )

    # Mock LLM returns buy with confidence exactly equal to the gate
    llm = MockLLMProvider(action="buy", quantity=5.0)
    from app.llm.base import Decision

    original_decide = llm.decide

    async def boundary_confidence_decide(context):
        d = await original_decide(context)
        return Decision(action=d.action, quantity=d.quantity, reasoning=d.reasoning, confidence=gate)

    llm.decide = boundary_confidence_decide

    portfolio = PortfolioManager(settings)
    engine = DecisionEngine(settings, llm, portfolio)

    async with test_db() as session:
        session.add(PriceCache(ticker="GOOG", price=Decimal("175.00")))
        session.add(TickerMetadata(ticker="GOOG", company_name="Alphabet", sector="Tech", market_cap_tier="large"))
        await session.commit()

    with patch("app.engine.decision.async_session_factory", test_db):
        event = PriceSpikeEvent(ticker="GOOG", pct_change=3.0, window_min=5)
        await engine._process_event(event)

    async with test_db() as session:
        result = await session.execute(select(Trade).where(Trade.ticker == "GOOG"))
        trade = result.scalar_one()
        # Confidence exactly at the gate should pass through as buy (not converted to hold)
        assert trade.action == "buy"

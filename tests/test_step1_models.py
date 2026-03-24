"""
Step 1: Config + Data Models — unit tests.
Covers SignalConfig defaults, Pydantic validation, Settings wiring,
DecisionOutcome instantiation, and CompositeSignal.trigger_detail.
"""
import os
import tempfile
from datetime import datetime, timezone

import pytest
import yaml

from app.config import Settings, SignalConfig, load_config
from app.engine.events import CompositeSignal, NewsEvent, PriceSpikeEvent
from app.models.calibration import DecisionOutcome

# ---------------------------------------------------------------------------
# SignalConfig defaults
# ---------------------------------------------------------------------------

class TestSignalConfigDefaults:
    def test_aggregation_disabled_by_default(self):
        cfg = SignalConfig()
        assert cfg.aggregation_enabled is False

    def test_aggregation_window_default(self):
        assert SignalConfig().aggregation_window_sec == 120

    def test_post_trade_cooldown_default(self):
        assert SignalConfig().post_trade_cooldown_min == 2

    def test_scoring_disabled_by_default(self):
        assert SignalConfig().scoring_enabled is False

    def test_min_signal_strength_default(self):
        assert SignalConfig().min_signal_strength == 0.4

    def test_normalize_context_disabled_by_default(self):
        assert SignalConfig().normalize_context is False

    def test_calibration_disabled_by_default(self):
        assert SignalConfig().calibration_enabled is False

    def test_calibration_lookback_days_default(self):
        assert SignalConfig().calibration_lookback_days == 30

    def test_confidence_gate_default(self):
        assert SignalConfig().confidence_gate == 0.0


# ---------------------------------------------------------------------------
# SignalConfig Pydantic validation (load from dict)
# ---------------------------------------------------------------------------

class TestSignalConfigValidation:
    def test_load_from_full_dict(self):
        data = {
            "aggregation_enabled": True,
            "aggregation_window_sec": 60,
            "post_trade_cooldown_min": 5,
            "scoring_enabled": True,
            "min_signal_strength": 0.6,
            "normalize_context": True,
            "calibration_enabled": True,
            "calibration_lookback_days": 14,
            "confidence_gate": 0.5,
        }
        cfg = SignalConfig.model_validate(data)
        assert cfg.aggregation_enabled is True
        assert cfg.aggregation_window_sec == 60
        assert cfg.post_trade_cooldown_min == 5
        assert cfg.scoring_enabled is True
        assert cfg.min_signal_strength == 0.6
        assert cfg.normalize_context is True
        assert cfg.calibration_enabled is True
        assert cfg.calibration_lookback_days == 14
        assert cfg.confidence_gate == 0.5

    def test_partial_dict_uses_defaults_for_missing_fields(self):
        cfg = SignalConfig.model_validate({"scoring_enabled": True})
        assert cfg.scoring_enabled is True
        # rest should remain default
        assert cfg.aggregation_enabled is False
        assert cfg.confidence_gate == 0.0

    def test_invalid_type_raises(self):
        with pytest.raises(Exception):
            SignalConfig.model_validate({"aggregation_window_sec": "not-an-int"})


# ---------------------------------------------------------------------------
# Settings accepts a signal: section
# ---------------------------------------------------------------------------

class TestSettingsSignalWiring:
    def test_settings_has_signal_field_with_defaults(self):
        s = Settings()
        assert hasattr(s, "signal")
        assert isinstance(s.signal, SignalConfig)
        assert s.signal.aggregation_enabled is False

    def test_settings_signal_can_be_overridden_via_dict(self):
        s = Settings.model_validate({"signal": {"aggregation_enabled": True, "confidence_gate": 0.3}})
        assert s.signal.aggregation_enabled is True
        assert s.signal.confidence_gate == 0.3
        # other settings untouched
        assert s.llm.provider == "ollama"

    def test_load_config_yaml_with_signal_section(self):
        config = {
            "signal": {
                "scoring_enabled": True,
                "min_signal_strength": 0.55,
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            path = f.name
        try:
            s = load_config(path)
            assert s.signal.scoring_enabled is True
            assert s.signal.min_signal_strength == 0.55
            # defaults preserved
            assert s.signal.aggregation_enabled is False
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# DecisionOutcome instantiation
# ---------------------------------------------------------------------------

class TestDecisionOutcome:
    def test_can_instantiate_with_required_fields(self):
        now = datetime.now(tz=timezone.utc)
        outcome = DecisionOutcome(
            trade_id=1,
            ticker="AAPL",
            action="buy",
            decided_at=now,
        )
        assert outcome.ticker == "AAPL"
        assert outcome.action == "buy"
        assert outcome.trade_id == 1
        assert outcome.decided_at == now

    def test_nullable_fields_default_to_none(self):
        now = datetime.now(tz=timezone.utc)
        outcome = DecisionOutcome(trade_id=2, ticker="TSLA", action="sell", decided_at=now)
        assert outcome.confidence is None
        assert outcome.price_at_decision is None
        assert outcome.signal_strength is None
        assert outcome.price_at_1h is None
        assert outcome.price_at_24h is None
        assert outcome.pct_change_1h is None
        assert outcome.pct_change_24h is None
        assert outcome.outcome_correct_1h is None
        assert outcome.outcome_correct_24h is None
        assert outcome.evaluated_at is None

    def test_tablename(self):
        assert DecisionOutcome.__tablename__ == "decision_outcomes"

    def test_exported_from_models_package(self):
        from app.models import DecisionOutcome as DO
        assert DO is DecisionOutcome


# ---------------------------------------------------------------------------
# CompositeSignal
# ---------------------------------------------------------------------------

class TestCompositeSignal:
    def _make_composite(self, direction="bullish", agreement=0.8):
        price_event = PriceSpikeEvent(ticker="AAPL", pct_change=3.0)
        news_event = NewsEvent(ticker="AAPL", headline="Big news")
        return CompositeSignal(
            ticker="AAPL",
            events=[price_event, news_event],
            dominant_direction=direction,
            agreement_score=agreement,
        )

    def test_trigger_type_is_composite(self):
        cs = self._make_composite()
        assert cs.trigger_type == "composite"

    def test_trigger_detail_format(self):
        cs = self._make_composite(direction="bullish")
        detail = cs.trigger_detail
        assert detail == "2 signals (price, news): bullish"

    def test_trigger_detail_with_no_events(self):
        cs = CompositeSignal(ticker="AAPL", events=[], dominant_direction="mixed")
        assert cs.trigger_detail == "0 signals (): mixed"

    def test_trigger_detail_single_event(self):
        evt = PriceSpikeEvent(ticker="TSLA", pct_change=-2.5)
        cs = CompositeSignal(ticker="TSLA", events=[evt], dominant_direction="bearish")
        assert cs.trigger_detail == "1 signals (price): bearish"

    def test_agreement_score_default(self):
        cs = CompositeSignal(ticker="AAPL")
        assert cs.agreement_score == 0.0

    def test_dominant_direction_default(self):
        cs = CompositeSignal(ticker="AAPL")
        assert cs.dominant_direction == "mixed"

    def test_events_list_default_is_empty(self):
        cs = CompositeSignal(ticker="AAPL")
        assert cs.events == []

    def test_events_list_is_not_shared_across_instances(self):
        cs1 = CompositeSignal(ticker="AAPL")
        cs2 = CompositeSignal(ticker="TSLA")
        cs1.events.append("x")
        assert cs2.events == []

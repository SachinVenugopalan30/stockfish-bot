import os
import tempfile

import yaml

from app.config import LLMConfig, Settings, TriggersConfig, load_config


def test_default_settings():
    settings = Settings()
    assert settings.llm.provider == "ollama"
    assert settings.portfolio.starting_cash == 100000.0
    assert settings.triggers.price_spike_pct == 2.0


def test_load_config_from_yaml():
    config = {
        "triggers": {"price_spike_pct": 3.5, "cooldown_min": 15},
        "llm": {"provider": "claude", "model": "claude-sonnet-4-6"},
        "portfolio": {"starting_cash": 50000.0, "max_position_pct": 5.0},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(config, f)
        path = f.name
    try:
        settings = load_config(path)
        assert settings.triggers.price_spike_pct == 3.5
        assert settings.triggers.cooldown_min == 15
        assert settings.llm.provider == "claude"
        assert settings.portfolio.starting_cash == 50000.0
    finally:
        os.unlink(path)


def test_load_config_missing_file():
    # Should return defaults when file is missing
    settings = load_config("/nonexistent/path/config.yaml")
    assert settings.llm.provider == "ollama"


def test_llm_config_providers():
    for provider in ["claude", "openai", "gemini", "ollama"]:
        cfg = LLMConfig(provider=provider)
        assert cfg.provider == provider


def test_triggers_config_defaults():
    t = TriggersConfig()
    assert t.price_spike_pct == 2.0
    assert t.price_spike_window_min == 5
    assert t.cooldown_min == 10
    assert t.reddit_min_upvotes == 50

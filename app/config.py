import functools
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class TriggersConfig(BaseModel):
    price_spike_pct: float = 2.0
    price_spike_window_min: int = 5
    cooldown_min: int = 10
    reddit_min_upvotes: int = 50


class LLMConfig(BaseModel):
    provider: str = "ollama"  # claude | openai | gemini | ollama
    model: str = "llama3"
    ollama_host: str = "http://localhost:11434"
    multi_agent: bool = False  # opt-in to 3-agent Research → Risk → Decision pipeline


class DataSourcesConfig(BaseModel):
    alpaca_feed: str = "iex"
    news_poll_interval_sec: int = 60
    reddit_subreddits: list[str] = ["wallstreetbets", "stocks", "investing"]


class PortfolioConfig(BaseModel):
    starting_cash: float = 100000.0
    max_position_pct: float = 10.0
    wallet_size: float = 0.0  # max capital the bot may deploy at once; 0 = no limit (uses starting_cash)


class SignalConfig(BaseModel):
    # Aggregation (Step 2)
    aggregation_enabled: bool = False
    aggregation_window_sec: int = 120
    post_trade_cooldown_min: int = 2

    # Scoring (Step 3)
    scoring_enabled: bool = False
    min_signal_strength: float = 0.4    # 0-1; below = skip event

    # Normalization (Step 4)
    normalize_context: bool = False

    # Calibration (Step 5)
    calibration_enabled: bool = False
    calibration_lookback_days: int = 30
    confidence_gate: float = 0.0        # 0 = disabled; e.g. 0.4 = reject sub-0.4 decisions


class Settings(BaseModel):
    triggers: TriggersConfig = TriggersConfig()
    llm: LLMConfig = LLMConfig()
    data_sources: DataSourcesConfig = DataSourcesConfig()
    portfolio: PortfolioConfig = PortfolioConfig()
    signal: SignalConfig = SignalConfig()


def load_config(path: str = "config.yaml") -> Settings:
    """Load Settings from a YAML file. Missing file falls back to defaults."""
    config_path = Path(path)
    raw: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("r") as fh:
            raw = yaml.safe_load(fh) or {}
    return Settings.model_validate(raw)


@functools.lru_cache(maxsize=1)
def get_settings(path: str = "config.yaml") -> Settings:
    """Cached accessor — returns the same Settings instance on repeated calls."""
    return load_config(path)

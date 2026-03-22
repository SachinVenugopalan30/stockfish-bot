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


class DataSourcesConfig(BaseModel):
    alpaca_feed: str = "iex"
    news_poll_interval_sec: int = 60
    reddit_subreddits: list[str] = ["wallstreetbets", "stocks", "investing"]


class PortfolioConfig(BaseModel):
    starting_cash: float = 100000.0
    max_position_pct: float = 10.0


class Settings(BaseModel):
    triggers: TriggersConfig = TriggersConfig()
    llm: LLMConfig = LLMConfig()
    data_sources: DataSourcesConfig = DataSourcesConfig()
    portfolio: PortfolioConfig = PortfolioConfig()


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

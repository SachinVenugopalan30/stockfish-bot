# stockfish-bot

Python backend for Stockfish — an event-driven paper trading bot that monitors news, price movements, and Reddit sentiment to make LLM-driven trading decisions.

## Overview

The bot runs a single async process with three concurrent monitors feeding a central decision engine:

- **Price monitor** — subscribes to Alpaca WebSocket, fires on spikes above a configurable threshold
- **News monitor** — polls RSS feeds (Reuters, CNBC, MarketWatch, Yahoo Finance) for ticker mentions
- **Reddit monitor** — streams new posts via PRAW from r/wallstreetbets, r/stocks, and r/investing
- **Decision engine** — deduplicates events by cooldown window, builds context, calls the configured LLM, writes trades to Postgres, and broadcasts results over WebSocket

All decisions are paper trades. No real money is ever placed.

## Stack

| Layer | Tech |
|---|---|
| Runtime | Python 3.12, asyncio |
| API | FastAPI + uvicorn |
| Database | PostgreSQL 18, SQLAlchemy 2.0 (async), Alembic |
| LLM | Claude, OpenAI, Gemini, or Ollama (configured in `config.yaml`) |
| Price data | alpaca-py, yfinance |
| News | feedparser |
| Social | praw |
| Scheduler | APScheduler |

## Configuration

Copy `config.yaml` and adjust as needed. The default provider is Ollama (no API key required):

```yaml
llm:
  provider: ollama   # claude | openai | gemini | ollama
  model: llama3
  ollama_host: http://host.docker.internal:11434

triggers:
  price_spike_pct: 2.0
  price_spike_window_min: 5
  cooldown_min: 10
  reddit_min_upvotes: 50

portfolio:
  starting_cash: 100000
  max_position_pct: 10
```

To switch to Claude, set `llm.provider: claude` and provide `ANTHROPIC_API_KEY` as an environment variable. The same pattern applies to OpenAI (`OPENAI_API_KEY`) and Gemini (`GOOGLE_API_KEY`).

## Running locally

**Requirements:** Docker, Docker Compose

```bash
docker compose -f docker-compose.dev.yml up
```

This starts the bot on port 8000 and a Postgres 18 instance on port 5433. The `app/` directory is volume-mounted for live reload.

Run migrations manually if needed:

```bash
uv run alembic upgrade head
```

## API

```
GET  /status                  Bot status, active LLM, monitor heartbeats
GET  /portfolio               Current value, cash, open positions
GET  /portfolio/snapshots     90-day portfolio value history
GET  /trades                  Trade history with reasoning and trigger detail
GET  /stats                   Aggregate stats: win rate, P&L, trade counts
GET  /signals/news            Recent headlines ingested
GET  /signals/skipped         Triggers suppressed by the cooldown window
WS   /ws                      Real-time stream of new trade decisions
```

## Development

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes (in Docker) | Postgres connection string |
| `ALPACA_API_KEY` | No | Enables live price feed (falls back to demo mode) |
| `ALPACA_SECRET_KEY` | No | Required alongside `ALPACA_API_KEY` |
| `ANTHROPIC_API_KEY` | If using Claude | |
| `OPENAI_API_KEY` | If using OpenAI | |
| `GOOGLE_API_KEY` | If using Gemini | |
| `REDDIT_CLIENT_ID` | No | Enables live Reddit stream (falls back to demo mode) |
| `REDDIT_CLIENT_SECRET` | No | Required alongside `REDDIT_CLIENT_ID` |

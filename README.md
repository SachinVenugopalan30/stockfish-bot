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

## Signal Quality

Four opt-in features improve the quality of signals before and after each LLM decision. All are disabled by default; add a `signal:` section to `config.yaml` to enable any of them independently.

```yaml
signal:
  # Event aggregation — collect events for the same ticker in a time window
  aggregation_enabled: true
  aggregation_window_sec: 120   # 2-minute window; first event starts the timer
  post_trade_cooldown_min: 2    # safety-net cooldown after a trade (replaces triggers.cooldown_min)

  # Signal scoring — skip weak events before calling the LLM
  scoring_enabled: true
  min_signal_strength: 0.4      # 0.0–1.0; events below this are logged as SkippedTrigger

  # Context normalization — inject a structured feature vector into the LLM prompt
  normalize_context: true

  # Post-decision calibration — track outcomes and inject accuracy stats into the prompt
  calibration_enabled: true
  calibration_lookback_days: 30
  confidence_gate: 0.4          # 0.0 = disabled; buy/sell below this confidence become hold
```

### Event Aggregation

Instead of processing every event the moment it arrives, the aggregator buffers all events for the same ticker within a configurable window. When the window expires, they are merged into a single `CompositeSignal` and forwarded to the decision engine. This prevents the LLM from being called multiple times in rapid succession for the same ticker and gives it richer context when multiple sources agree.

- A single event in the window is unwrapped and passed as its original type (no wrapping overhead).
- `dominant_direction` is `bullish`, `bearish`, or `mixed` based on the mean signed direction of all events.
- `agreement_score` ranges from -1 (contradicting sources) to +1 (all sources agree).

### Signal Scoring

Each event is scored 0.0–1.0 before the LLM is called. The score is based on:

| Source | Components |
|--------|-----------|
| Price spike | Magnitude, velocity, RSI/MACD alignment, recent sentiment alignment |
| News | Sentiment magnitude, source credibility, novelty, technical alignment |
| Reddit | Sentiment magnitude, trend consistency, technical alignment, price momentum |
| CompositeSignal | Mean of component scores ± agreement bonus/penalty |

Events below `min_signal_strength` are skipped entirely and logged as `SkippedTrigger`. For events that pass, the score is stored on the `Trade` record (`signal_strength` column) and included in the LLM prompt.

### Context Normalization

When enabled, the bot computes a structured feature vector from live market data and appends it to the LLM prompt:

```
=== SIGNAL FEATURES (-1.0 to +1.0) ===
  Price momentum:      +0.45
  Technical alignment: -0.20
  Sentiment composite: +0.32
  Portfolio pressure:  +0.80
  Signal strength:     0.72
Note: positive = bullish/room-to-buy, negative = bearish/should-reduce
```

All features are normalised to the [-1, +1] range (signal strength to [0, 1]). Missing data (e.g. no technical indicators yet) defaults to 0.0.

### Post-Decision Calibration

The calibration system tracks whether past decisions were correct and injects a running accuracy summary into the LLM prompt, letting the model learn from its own history.

**How it works:**

1. After each trade, a `DecisionOutcome` row is created with the price at decision time.
2. A background job (every 30 min) fills in the price 1 h and 24 h after each decision.
3. Correctness is determined by: buy correct = price up, sell correct = price down, hold correct = price within ±1%.
4. The summary is prepended to the next LLM prompt:

```
=== CALIBRATION (last 30 days, 50 decisions) ===
  Accuracy: 1h=62% (31/50) | 24h=58% (29/50)
  By action: BUY 1h=65% 24h=60% (20) | SELL 1h=70% 24h=55% (15) | HOLD 1h=50% 24h=58% (15)
  By confidence: High(>0.7) 1h=72% (18) | Med(0.4-0.7) 1h=58% (22) | Low(<0.4) 1h=40% (10)
```

**Confidence gate:** If `confidence_gate > 0`, any buy or sell decision with confidence strictly below the gate is automatically converted to hold before execution. A decision exactly at the threshold is allowed through.

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

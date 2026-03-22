# Build stage
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml .
COPY uv.lock* ./

# Install dependencies (no dev)
RUN uv sync --no-dev --frozen || uv sync --no-dev

# Runtime stage
FROM python:3.12-slim AS runtime

WORKDIR /app

# Install uv in runtime for running commands
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy virtual env from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY . .

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# syntax=docker/dockerfile:1

# PulseAI backend image — used by the api, worker, and scheduler services.
# The same image runs all three processes; docker-compose overrides CMD per service.
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (cached unless pyproject/uv.lock change).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy the application, migrations, and migration config.
COPY backend ./backend
COPY migrations ./migrations
COPY alembic.ini ./

EXPOSE 8000

# Default command = API server (worker/scheduler override via compose).
CMD ["uv", "run", "--no-sync", "pulseai-api"]

# Faktomat — single image, two entrypoints (app + worker).
#
# The same image runs both the FastAPI app (uvicorn) and the arq worker;
# `docker-compose.prod.yml` overrides the command per service. Keeps the
# build cache hot across both processes.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps: libxml2/libxslt for lxml (JPK_FA), libpq for asyncpg, curl
# for healthchecks. Slim image misses these by default.
RUN apt-get update && apt-get install --no-install-recommends -y \
        libxml2 \
        libxslt1.1 \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy lock-ish files first so dep install caches.
COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install . && \
    pip install ".[prod]"

# App source after deps — code churns more often than deps.
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY schemas/ ./schemas/
COPY prompts/ ./prompts/
COPY enqueue.py ./

# Healthcheck used by Caddy upstream + compose.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

EXPOSE 8000

# Default = the web app. Override in compose for the worker service.
CMD ["uvicorn", "src.app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]

"""Integration test fixtures.

Pre-conditions before running:
    - Postgres running (docker compose up -d postgres)
    - Test DB created:
        docker exec invoice_idp_postgres psql -U invoice_idp -d invoice_idp \
            -c "CREATE DATABASE invoice_idp_test;"
      (one-shot; the conftest then applies migrations on each pytest session)

`TEST_DATABASE_URL` can override the default; otherwise tests use
`invoice_idp_test` on the local docker Postgres.
"""

from __future__ import annotations

import asyncio
import os
import sys

# Windows defaults to ProactorEventLoop, which conflicts with asyncpg's
# socket handling and produces flaky `NoneType has no attribute 'send'`
# teardown errors in pytest. Force Selector before any asyncio touchpoint.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Force test DB before any `src.*` imports cache settings.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://invoice_idp:invoice_idp@localhost:5432/invoice_idp_test",
)
os.environ.setdefault("SESSION_SECRET", "test-session-secret-not-for-prod")
os.environ.setdefault("CSRF_SECRET", "test-csrf-secret-not-for-prod")
os.environ.setdefault("APP_BASE_URL", "http://test")
# Force ConsoleEmailer regardless of .env Postmark config — real
# Postmark calls from tests would fail (sender approval / test mode).
os.environ["POSTMARK_API_TOKEN"] = ""
os.environ["POSTMARK_FROM_EMAIL"] = ""
# `session_cookie_secure` defaults to False in dev/test so httpx
# ASGI transport (http://) gets the session cookie back.

import pytest_asyncio  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from src.app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from src.app.db import Base, SessionLocal, engine  # noqa: E402
from src.app.main import app  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _apply_migrations() -> None:
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    command.upgrade(cfg, "head")


_apply_migrations()


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Per-test session, with all tables truncated up-front for isolation."""
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))

    async with SessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncClient:
    """Async HTTP client bound to the FastAPI app, sharing the test DB."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

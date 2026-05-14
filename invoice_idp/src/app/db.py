"""SQLAlchemy async engine, session factory, and declarative Base.

`Base` is shared by every model in `src/models/`. FastAPI handlers
get a session via `Depends(get_session)` — one session per request,
rolled back automatically if the handler raises.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.app.config import get_settings


class Base(DeclarativeBase):
    """Shared declarative base for all SQLAlchemy models."""


_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=_settings.debug,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency — yields an AsyncSession, rolls back on exception."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

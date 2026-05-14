"""Common FastAPI dependencies for authenticated endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.db import get_session
from src.models.user import User


async def get_current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Resolve the logged-in user from the session cookie.

    Raises 401 if no session, the session is malformed, or the user no
    longer exists (e.g. soft-deleted while session was live).
    """
    user_id_str = request.session.get("user_id")
    if not user_id_str:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    try:
        user_id = UUID(user_id_str)
    except ValueError:
        request.session.clear()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session") from None

    user = await session.scalar(
        select(User).where(User.id == user_id, User.deleted_at.is_(None))
    )
    if user is None:
        request.session.clear()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session invalid")
    return user


async def require_verified_email(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Variant that additionally requires the email to be verified."""
    if not current_user.email_verified:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Email verification required",
        )
    return current_user

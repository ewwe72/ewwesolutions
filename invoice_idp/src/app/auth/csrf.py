"""CSRF protection — synchronizer token pattern.

Token is stored in the signed session cookie and must be echoed back
on every state-changing request via the `X-CSRF-Token` header. Since
the session cookie is HttpOnly + signed, JavaScript on a malicious
origin can't read it; the attacker therefore can't forge a matching
header.

Wire it on authenticated mutations:
    @router.post("/something", dependencies=[Depends(verify_csrf)])
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status

CSRF_HEADER_NAME = "x-csrf-token"
CSRF_SESSION_KEY = "csrf_token"


def get_or_create_csrf_token(request: Request) -> str:
    """Return the session's CSRF token, generating one if missing."""
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


async def verify_csrf(request: Request) -> None:
    """FastAPI dependency — raises 403 on missing/mismatched CSRF token."""
    session_token = request.session.get(CSRF_SESSION_KEY)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if not session_token or not header_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing")
    if not secrets.compare_digest(session_token, header_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token mismatch")

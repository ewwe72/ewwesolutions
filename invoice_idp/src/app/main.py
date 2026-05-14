"""FastAPI application entrypoint.

Phase 2 surface:
    - /health (liveness)
    - /auth/* (signup, verify, login, logout, password reset, /me, /csrf)

Dev:
    uvicorn src.app.main:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from src.app.api.invoices import router as invoices_router
from src.app.api.webhooks import router as webhooks_router
from src.app.auth.routes import router as auth_router
from src.app.config import get_settings
from src.app.web.routes import router as web_router

settings = get_settings()

app = FastAPI(
    title="Invoice IDP",
    version="0.1.0",
    description="AI-powered invoice data extraction for Polish accounting.",
    debug=settings.debug,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret or "dev-fallback-do-not-use-in-prod",
    session_cookie="session",
    max_age=60 * 60 * 24 * 30,    # 30 days, per spec §10
    same_site="lax",
    https_only=settings.session_cookie_secure,
)

app.include_router(auth_router)
app.include_router(invoices_router)
app.include_router(webhooks_router)
app.include_router(web_router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """Liveness probe — UptimeRobot hits this every 60s (no auth)."""
    return {"status": "ok"}

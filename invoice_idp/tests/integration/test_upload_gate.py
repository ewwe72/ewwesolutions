"""Upload gate: /app/wgraj requires email-verified + positive credit
balance. Both GET (form render) and POST (upload submit) are gated.

Phase 6 originally added a phone-verify (SMS-OTP) layer; that was
deferred to Phase 7+ on 2026-05-14. The gate now uses the
email-verified flag (Postmark verifies on signup) — anyone who has
clicked the activation link AND has a non-zero balance can upload.

Failing the gate redirects:
  - email not verified → /app?reason=email_verify
  - empty balance     → /app/billing?reason=empty
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.org import Org
from src.models.user import User


async def _signup_and_login(
    client: AsyncClient, db_session: AsyncSession, email: str,
    *, email_verified: bool = True,
) -> User:
    """Create the user, optionally mark email verified, then log in."""
    await client.post(
        "/auth/signup",
        json={"email": email, "password": "supersecret123"},
    )
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    if email_verified:
        user.email_verified = True
        user.email_verification_token = None
        await db_session.commit()
    await client.post(
        "/auth/login",
        json={"email": email, "password": "supersecret123"},
    )
    return user


async def _set_balance(db_session: AsyncSession, user: User, grosze: int) -> None:
    org = await db_session.scalar(select(Org).where(Org.id == user.org_id))
    assert org is not None
    org.credit_balance_grosze = grosze
    await db_session.commit()


@pytest.mark.asyncio
async def test_upload_get_redirects_when_email_unverified(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _signup_and_login(
        client, db_session, "gate-no-email@example.com",
        email_verified=False,
    )
    resp = await client.get("/app/wgraj", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/app?reason=email_verify"


@pytest.mark.asyncio
async def test_upload_get_redirects_when_balance_empty(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _signup_and_login(client, db_session, "gate-no-credit@example.com")
    # Balance defaults to 0; email verified by helper.
    resp = await client.get("/app/wgraj", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/app/billing?reason=empty"


@pytest.mark.asyncio
async def test_upload_get_renders_when_gate_passes(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    user = await _signup_and_login(client, db_session, "gate-ok@example.com")
    await _set_balance(db_session, user, 5000)
    resp = await client.get("/app/wgraj")
    assert resp.status_code == 200, resp.text
    assert 'name="pdf"' in resp.text  # form rendered


@pytest.mark.asyncio
async def test_upload_post_blocked_when_email_unverified(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _signup_and_login(
        client, db_session, "gate-post-email@example.com",
        email_verified=False,
    )
    # Even with a file in the request, the gate should fire first.
    resp = await client.post(
        "/app/wgraj",
        files={"pdf": ("a.pdf", b"%PDF-1.4\n", "application/pdf")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/app?reason=email_verify"


@pytest.mark.asyncio
async def test_upload_post_blocked_when_balance_empty(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _signup_and_login(client, db_session, "gate-post-credit@example.com")
    resp = await client.post(
        "/app/wgraj",
        files={"pdf": ("a.pdf", b"%PDF-1.4\n", "application/pdf")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/app/billing?reason=empty"


@pytest.mark.asyncio
async def test_layout_shows_email_banner_when_unverified(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    """The global email-verify banner in _layout.html should render on
    any authenticated page while email is unverified."""
    await _signup_and_login(
        client, db_session, "banner-email@example.com",
        email_verified=False,
    )
    resp = await client.get("/app")
    assert resp.status_code == 200, resp.text
    assert "nie jest jeszcze zweryfikowany" in resp.text

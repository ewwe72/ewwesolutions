"""Integration tests for Phase 6 phone-verify endpoints.

Uses the in-tree ConsoleSmsVerifier (auto-selected when Twilio env vars
are absent). The dev code is the last 6 digits of the phone number —
see `src/app/sms.py:_dev_code_for`.

POST /auth/phone/start { phone }  → stamps phone_number + sent_at
POST /auth/phone/check { code }   → stamps phone_verified_at on match
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.sms import _dev_code_for
from src.models.audit import AuditEvent
from src.models.user import User


async def _login(
    client: AsyncClient, db_session: AsyncSession, email: str,
) -> tuple[User, str]:
    """Sign up + verify email + log in. Returns (user, csrf_token)."""
    await client.post("/auth/signup", json={"email": email, "password": "supersecret123"})
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    user.email_verified = True
    user.email_verification_token = None
    await db_session.commit()
    await client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    return user, str(csrf)


@pytest.mark.asyncio
async def test_phone_start_stamps_user(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    user, csrf = await _login(client, db_session, "phone-start@example.com")
    resp = await client.post(
        "/auth/phone/start",
        json={"phone": "+48 600 123 456"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text

    await db_session.refresh(user)
    assert user.phone_number == "+48600123456"  # normalised
    assert user.phone_verification_sent_at is not None
    assert user.phone_verified_at is None


@pytest.mark.asyncio
async def test_phone_start_rejects_non_e164(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    _, csrf = await _login(client, db_session, "phone-bad@example.com")
    resp = await client.post(
        "/auth/phone/start",
        json={"phone": "600123456"},  # no leading +
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422
    assert "E.164" in resp.text


@pytest.mark.asyncio
async def test_phone_start_rate_limits_resend(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    _, csrf = await _login(client, db_session, "phone-rate@example.com")
    r1 = await client.post(
        "/auth/phone/start",
        json={"phone": "+48600123456"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r1.status_code == 200, r1.text

    r2 = await client.post(
        "/auth/phone/start",
        json={"phone": "+48600123456"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r2.status_code == 429
    assert "Poczekaj" in r2.text


@pytest.mark.asyncio
async def test_phone_start_allows_resend_after_cooldown(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    user, csrf = await _login(client, db_session, "phone-cooldown@example.com")
    r1 = await client.post(
        "/auth/phone/start",
        json={"phone": "+48600123456"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r1.status_code == 200, r1.text

    # Backdate the stamp past the cooldown without sleeping the test.
    user.phone_verification_sent_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    await db_session.commit()

    r2 = await client.post(
        "/auth/phone/start",
        json={"phone": "+48600123456"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r2.status_code == 200, r2.text


@pytest.mark.asyncio
async def test_phone_check_verifies_with_dev_code(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    user, csrf = await _login(client, db_session, "phone-ok@example.com")
    await client.post(
        "/auth/phone/start",
        json={"phone": "+48600123456"},
        headers={"X-CSRF-Token": csrf},
    )
    code = _dev_code_for("+48600123456")
    resp = await client.post(
        "/auth/phone/check",
        json={"code": code},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text

    await db_session.refresh(user)
    assert user.phone_verified_at is not None

    events = list(await db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "auth.phone_verified")
    ))
    assert len(events) == 1


@pytest.mark.asyncio
async def test_phone_check_rejects_wrong_code(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    user, csrf = await _login(client, db_session, "phone-wrong@example.com")
    await client.post(
        "/auth/phone/start",
        json={"phone": "+48600123456"},
        headers={"X-CSRF-Token": csrf},
    )
    resp = await client.post(
        "/auth/phone/check",
        json={"code": "000000"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 400
    await db_session.refresh(user)
    assert user.phone_verified_at is None

    failed = list(await db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "auth.phone_check_failed")
    ))
    assert len(failed) == 1


@pytest.mark.asyncio
async def test_phone_check_before_start_409s(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    _, csrf = await _login(client, db_session, "phone-pre@example.com")
    resp = await client.post(
        "/auth/phone/check",
        json={"code": "123456"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_phone_start_blocks_when_already_verified(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    user, csrf = await _login(client, db_session, "phone-done@example.com")
    user.phone_number = "+48600123456"
    user.phone_verified_at = datetime.now(timezone.utc)
    await db_session.commit()

    resp = await client.post(
        "/auth/phone/start",
        json={"phone": "+48600123456"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_phone_endpoints_require_csrf(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    _, _ = await _login(client, db_session, "phone-csrf@example.com")
    # No X-CSRF-Token header → 403 from verify_csrf dependency.
    resp = await client.post(
        "/auth/phone/start", json={"phone": "+48600123456"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_phone_endpoints_require_login(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/auth/phone/start",
        json={"phone": "+48600123456"},
        headers={"X-CSRF-Token": "anything"},
    )
    # get_current_user raises 401 before CSRF check completes.
    assert resp.status_code in (401, 403)

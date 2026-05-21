"""Integration tests for the org-settings page (Phase 4 chunk 4).

GET  /app/ustawienia — render with current Org values pre-filled.
POST /app/ustawienia — validate + save Org.name/nip/regon/kod_urzedu.

The full validation lives in `_validate_settings_form` in `src/app/web/routes.py`;
these tests cover the wire path: CSRF gate, persistence, validation error
re-render, audit-log emission, login gate.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.audit import AuditEvent
from src.models.org import Org
from src.models.user import User


async def _login_with_csrf(
    client: AsyncClient, db_session: AsyncSession, email: str,
) -> str:
    await client.post("/auth/signup", json={"email": email, "password": "supersecret123"})
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    user.email_verified = True
    user.email_verification_token = None
    await db_session.commit()
    await client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    csrf = await client.get("/auth/csrf")
    return str(csrf.json()["csrf_token"])


async def _org_for(db_session: AsyncSession, email: str) -> Org:
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    org = await db_session.scalar(select(Org).where(Org.id == user.org_id))
    assert org is not None
    return org


@pytest.mark.asyncio
async def test_settings_get_requires_login(client: AsyncClient) -> None:
    resp = await client.get("/app/ustawienia", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_settings_get_renders_current_values(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    email = "settings-get@example.com"
    await _login_with_csrf(client, db_session, email)
    org = await _org_for(db_session, email)
    org.nip = "1234567819"
    org.kod_urzedu = "0202"
    await db_session.commit()

    resp = await client.get("/app/ustawienia")
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert 'value="1234567819"' in body
    assert 'value="0202"' in body
    # Form uses the Polish slug.
    assert 'action="/app/ustawienia"' in body


@pytest.mark.asyncio
async def test_settings_post_persists_and_audits(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    email = "settings-save@example.com"
    csrf = await _login_with_csrf(client, db_session, email)

    resp = await client.post(
        "/app/ustawienia",
        data={
            "csrf_token": csrf,
            "name": "Test Org sp. z o.o.",
            "nip": "PL 123-456-78-19",
            "regon": "",
            "kod_urzedu": "0202",
        },
    )
    assert resp.status_code == 200, resp.text
    assert "Zapisano." in resp.text

    org = await _org_for(db_session, email)
    await db_session.refresh(org)
    assert org.name == "Test Org sp. z o.o."
    assert org.nip == "1234567819"  # normalised
    assert org.regon is None
    assert org.kod_urzedu == "0202"

    events = list(await db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "org.settings_updated")
    ))
    assert len(events) == 1
    assert sorted(events[0].payload["changed_fields"]) == [
        "kod_urzedu", "name", "nip",
    ]


@pytest.mark.asyncio
async def test_settings_post_rejects_bad_kod_urzedu(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    email = "settings-bad-kod@example.com"
    csrf = await _login_with_csrf(client, db_session, email)
    org_before = await _org_for(db_session, email)
    original_kod = org_before.kod_urzedu

    resp = await client.post(
        "/app/ustawienia",
        data={
            "csrf_token": csrf,
            "name": "Test",
            "nip": "",
            "regon": "",
            "kod_urzedu": "12",  # only 2 digits
        },
    )
    assert resp.status_code == 400
    assert "Kod urzędu skarbowego" in resp.text
    # DB unchanged.
    await db_session.refresh(org_before)
    assert org_before.kod_urzedu == original_kod


@pytest.mark.asyncio
async def test_settings_post_rejects_bad_nip(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    email = "settings-bad-nip@example.com"
    csrf = await _login_with_csrf(client, db_session, email)

    resp = await client.post(
        "/app/ustawienia",
        data={
            "csrf_token": csrf,
            "name": "Test",
            "nip": "1234567810",  # bad checksum
            "regon": "",
            "kod_urzedu": "",
        },
    )
    assert resp.status_code == 400
    assert "NIP jest niepoprawny" in resp.text


@pytest.mark.asyncio
async def test_settings_post_requires_name(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    email = "settings-no-name@example.com"
    csrf = await _login_with_csrf(client, db_session, email)

    resp = await client.post(
        "/app/ustawienia",
        data={
            "csrf_token": csrf,
            "name": "   ",
            "nip": "",
            "regon": "",
            "kod_urzedu": "",
        },
    )
    assert resp.status_code == 400
    assert "Nazwa organizacji" in resp.text


@pytest.mark.asyncio
async def test_settings_post_requires_csrf(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    email = "settings-csrf@example.com"
    await _login_with_csrf(client, db_session, email)

    resp = await client.post(
        "/app/ustawienia",
        data={
            "csrf_token": "wrong-token",
            "name": "Test",
            "nip": "",
            "regon": "",
            "kod_urzedu": "",
        },
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_settings_post_noop_skips_audit(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    """Saving with values that match the current state should not emit
    an audit event — keeps the audit log signal-to-noise high."""
    email = "settings-noop@example.com"
    csrf = await _login_with_csrf(client, db_session, email)
    org = await _org_for(db_session, email)

    resp = await client.post(
        "/app/ustawienia",
        data={
            "csrf_token": csrf,
            "name": org.name,
            "nip": "",
            "regon": "",
            "kod_urzedu": "",
        },
    )
    assert resp.status_code == 200, resp.text

    events = list(await db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "org.settings_updated")
    ))
    assert len(events) == 0

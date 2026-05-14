"""Integration tests for the auth flow.

End-to-end: signup → verify-email → login → /me → logout, plus the
forgot-password / reset-password flow and the obvious negative cases.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.user import User


@pytest.mark.asyncio
async def test_signup_creates_user_and_org(client: AsyncClient, db_session: AsyncSession) -> None:
    resp = await client.post(
        "/auth/signup",
        json={"email": "alice@example.com", "password": "supersecret123"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert "user_id" in body

    user = await db_session.scalar(select(User).where(User.email == "alice@example.com"))
    assert user is not None
    assert user.email_verified is False
    assert user.email_verification_token
    assert user.org_id


@pytest.mark.asyncio
async def test_signup_rejects_duplicate(client: AsyncClient) -> None:
    payload = {"email": "dup@example.com", "password": "supersecret123"}
    first = await client.post("/auth/signup", json=payload)
    assert first.status_code == 201
    second = await client.post("/auth/signup", json=payload)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_signup_validates_password_length(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/signup",
        json={"email": "short@example.com", "password": "tiny"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_full_signup_verify_login_logout_flow(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await client.post(
        "/auth/signup",
        json={"email": "bob@example.com", "password": "supersecret123"},
    )

    user = await db_session.scalar(select(User).where(User.email == "bob@example.com"))
    assert user is not None
    token = user.email_verification_token
    assert token is not None

    resp = await client.get(f"/auth/verify-email?token={token}")
    assert resp.status_code == 200

    me_unauth = await client.get("/auth/me")
    assert me_unauth.status_code == 401

    login = await client.post(
        "/auth/login",
        json={"email": "bob@example.com", "password": "supersecret123"},
    )
    assert login.status_code == 200
    assert login.json()["email_verified"] is True

    me = await client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "bob@example.com"

    csrf = await client.get("/auth/csrf")
    csrf_token = csrf.json()["csrf_token"]

    logout_no_csrf = await client.post("/auth/logout")
    assert logout_no_csrf.status_code == 403

    logout = await client.post("/auth/logout", headers={"X-CSRF-Token": csrf_token})
    assert logout.status_code == 200

    me_after = await client.get("/auth/me")
    assert me_after.status_code == 401


@pytest.mark.asyncio
async def test_verify_email_rejects_bad_token(client: AsyncClient) -> None:
    resp = await client.get("/auth/verify-email?token=nope-not-a-real-token")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_login_rejects_wrong_password(client: AsyncClient) -> None:
    await client.post(
        "/auth/signup",
        json={"email": "wrong@example.com", "password": "supersecret123"},
    )
    resp = await client.post(
        "/auth/login",
        json={"email": "wrong@example.com", "password": "WRONGpassword"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_password_reset_flow(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await client.post(
        "/auth/signup",
        json={"email": "reset@example.com", "password": "oldpassword123"},
    )

    forgot = await client.post(
        "/auth/forgot-password",
        json={"email": "reset@example.com"},
    )
    assert forgot.status_code == 200

    await db_session.commit()       # commit any pending state from setup
    user = await db_session.scalar(
        select(User).where(User.email == "reset@example.com")
    )
    await db_session.refresh(user)  # re-read after forgot-password commit
    assert user is not None
    reset_token = user.password_reset_token
    assert reset_token is not None

    reset = await client.post(
        "/auth/reset-password",
        json={"token": reset_token, "password": "newpassword123"},
    )
    assert reset.status_code == 200

    fail_old = await client.post(
        "/auth/login",
        json={"email": "reset@example.com", "password": "oldpassword123"},
    )
    assert fail_old.status_code == 401

    ok_new = await client.post(
        "/auth/login",
        json={"email": "reset@example.com", "password": "newpassword123"},
    )
    assert ok_new.status_code == 200


@pytest.mark.asyncio
async def test_forgot_password_does_not_leak_account_existence(
    client: AsyncClient,
) -> None:
    """Both real and fake emails must return the same generic message."""
    real = await client.post(
        "/auth/signup",
        json={"email": "real@example.com", "password": "supersecret123"},
    )
    assert real.status_code == 201

    real_forgot = await client.post(
        "/auth/forgot-password", json={"email": "real@example.com"},
    )
    fake_forgot = await client.post(
        "/auth/forgot-password", json={"email": "nobody@example.com"},
    )
    assert real_forgot.status_code == fake_forgot.status_code == 200
    assert real_forgot.json() == fake_forgot.json()

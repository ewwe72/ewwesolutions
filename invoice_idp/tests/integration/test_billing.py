"""Integration tests for Phase 6 billing top-up flow.

GET  /app/billing            — balance + amount buttons
POST /app/billing/topup      — Stripe Checkout Session create + 303

Uses ConsoleStripeClient (auto-selected when STRIPE_SECRET_KEY is empty).
The console client returns `http://localhost:8000/__dev/checkout/cs_dev_*`
URLs, so the test asserts on prefix shape rather than a real
checkout.stripe.com host.
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
async def test_billing_get_requires_login(client: AsyncClient) -> None:
    resp = await client.get("/app/billing", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_billing_get_renders_balance_and_amounts(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    email = "billing-get@example.com"
    await _login_with_csrf(client, db_session, email)
    org = await _org_for(db_session, email)
    org.credit_balance_grosze = 1234
    await db_session.commit()

    resp = await client.get("/app/billing")
    assert resp.status_code == 200, resp.text
    body = resp.text
    # Template renders "12.34<span …>PLN</span>" — assert pieces separately.
    assert "12.34" in body  # balance pretty-print
    assert ">PLN<" in body  # currency marker (span-wrapped)
    # Top-up amounts ship as hidden inputs in grosze; the visible label
    # is `{{ amount // 100 }}` followed by a span. Check the input.
    for grosze in (2000, 5000, 10000):
        assert f'value="{grosze}"' in body, (
            f"top-up button for {grosze} grosze missing"
        )
    assert 'action="/app/billing/topup"' in body


@pytest.mark.asyncio
async def test_topup_redirects_to_checkout_url(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    email = "topup-ok@example.com"
    csrf = await _login_with_csrf(client, db_session, email)

    resp = await client.post(
        "/app/billing/topup",
        data={"csrf_token": csrf, "amount_grosze": "5000"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    loc = resp.headers["location"]
    assert loc.startswith("http://localhost:8000/__dev/checkout/cs_dev_")

    # Stripe customer id stamped on org.
    org = await _org_for(db_session, email)
    await db_session.refresh(org)
    assert org.stripe_customer_id is not None
    assert org.stripe_customer_id.startswith("cus_dev_")

    # Audit event emitted with amount + idem_key.
    events = list(await db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "billing.topup_started")
    ))
    assert len(events) == 1
    assert events[0].payload["amount_grosze"] == 5000
    assert isinstance(events[0].payload["idem_key"], str)
    assert len(events[0].payload["idem_key"]) == 32  # uuid4 hex


@pytest.mark.asyncio
async def test_topup_rejects_arbitrary_amount(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    csrf = await _login_with_csrf(client, db_session, "topup-bad@example.com")
    resp = await client.post(
        "/app/billing/topup",
        data={"csrf_token": csrf, "amount_grosze": "1234"},
    )
    assert resp.status_code == 400
    assert "Nieobsługiwana kwota" in resp.text


@pytest.mark.asyncio
async def test_topup_requires_csrf(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _login_with_csrf(client, db_session, "topup-csrf@example.com")
    resp = await client.post(
        "/app/billing/topup",
        data={"csrf_token": "wrong", "amount_grosze": "2000"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_topup_reuses_customer_id_on_second_call(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    email = "topup-reuse@example.com"
    csrf = await _login_with_csrf(client, db_session, email)

    r1 = await client.post(
        "/app/billing/topup",
        data={"csrf_token": csrf, "amount_grosze": "2000"},
        follow_redirects=False,
    )
    assert r1.status_code == 303
    org = await _org_for(db_session, email)
    await db_session.refresh(org)
    first_cid = org.stripe_customer_id
    assert first_cid is not None

    r2 = await client.post(
        "/app/billing/topup",
        data={"csrf_token": csrf, "amount_grosze": "10000"},
        follow_redirects=False,
    )
    assert r2.status_code == 303
    await db_session.refresh(org)
    assert org.stripe_customer_id == first_cid  # no churn

    events = list(await db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "billing.topup_started")
    ))
    assert len(events) == 2
    # Distinct idempotency keys.
    keys = {e.payload["idem_key"] for e in events}
    assert len(keys) == 2


@pytest.mark.asyncio
async def test_topup_requires_login(client: AsyncClient) -> None:
    resp = await client.post(
        "/app/billing/topup",
        data={"csrf_token": "anything", "amount_grosze": "2000"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"

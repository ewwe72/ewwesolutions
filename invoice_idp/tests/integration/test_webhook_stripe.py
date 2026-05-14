"""Integration tests for POST /webhooks/stripe.

The test conftest does not set `STRIPE_WEBHOOK_SECRET`, so the handler
runs in dev-trust-body mode (mirrors the operator's pre-Stripe-key dev
loop). Real-mode SDK verification is exercised only when the operator
sets the env var; covered by the unit-level construct_event call in
the SDK itself.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.audit import AuditEvent
from src.models.org import Org
from src.models.user import User


async def _org_with_customer(
    db_session: AsyncSession, *, customer_id: str = "cus_dev_abc",
) -> Org:
    """Seed a user + org with a Stripe customer id ready for crediting."""
    org = Org(name="Test sp. z o.o.", stripe_customer_id=customer_id)
    db_session.add(org)
    await db_session.flush()
    user = User(
        email=f"webhook-{uuid4().hex[:8]}@example.com",
        password_hash="argon2-dummy",
        email_verified=True,
        org_id=org.id,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(org)
    return org


def _checkout_completed_event(
    *,
    customer_id: str,
    amount_grosze: int,
    idem_key: str,
    payment_status: str = "paid",
    event_id: str = "evt_test_123",
) -> dict[str, object]:
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_session_123",
                "object": "checkout.session",
                "customer": customer_id,
                "amount_total": amount_grosze,
                "payment_status": payment_status,
                "metadata": {
                    "app": "faktomat",
                    "topup_idem_key": idem_key,
                },
            },
        },
    }


@pytest.mark.asyncio
async def test_webhook_credits_balance_on_completed(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    org = await _org_with_customer(db_session, customer_id="cus_dev_credit_ok")
    event = _checkout_completed_event(
        customer_id="cus_dev_credit_ok",
        amount_grosze=5000,
        idem_key="idem-credit-1",
    )
    resp = await client.post("/webhooks/stripe", content=json.dumps(event))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"message": "credited"}

    await db_session.refresh(org)
    assert org.credit_balance_grosze == 5000

    audits = list(await db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "billing.topup_credited")
    ))
    assert len(audits) == 1
    assert audits[0].payload["idem_key"] == "idem-credit-1"
    assert audits[0].payload["amount_grosze"] == 5000


@pytest.mark.asyncio
async def test_webhook_is_idempotent_on_replay(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    org = await _org_with_customer(db_session, customer_id="cus_dev_replay")
    event = _checkout_completed_event(
        customer_id="cus_dev_replay",
        amount_grosze=2000,
        idem_key="idem-replay-1",
    )

    r1 = await client.post("/webhooks/stripe", content=json.dumps(event))
    assert r1.status_code == 200
    assert r1.json()["message"] == "credited"

    # Stripe retries with the same event payload — must not double-credit.
    r2 = await client.post("/webhooks/stripe", content=json.dumps(event))
    assert r2.status_code == 200
    assert r2.json()["message"] == "noop"

    await db_session.refresh(org)
    assert org.credit_balance_grosze == 2000  # not 4000


@pytest.mark.asyncio
async def test_webhook_ignores_non_completed_events(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    org = await _org_with_customer(db_session, customer_id="cus_dev_ignored")
    event = {
        "id": "evt_other",
        "type": "payment_intent.created",  # not our handler's concern
        "data": {"object": {"customer": "cus_dev_ignored"}},
    }
    resp = await client.post("/webhooks/stripe", content=json.dumps(event))
    assert resp.status_code == 200
    assert resp.json()["message"] == "ignored"
    await db_session.refresh(org)
    assert org.credit_balance_grosze == 0


@pytest.mark.asyncio
async def test_webhook_ignores_unpaid_sessions(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    org = await _org_with_customer(db_session, customer_id="cus_dev_unpaid")
    event = _checkout_completed_event(
        customer_id="cus_dev_unpaid",
        amount_grosze=2000,
        idem_key="idem-unpaid",
        payment_status="unpaid",
    )
    resp = await client.post("/webhooks/stripe", content=json.dumps(event))
    assert resp.status_code == 200
    assert resp.json()["reason"] == "payment_status_not_paid"
    await db_session.refresh(org)
    assert org.credit_balance_grosze == 0


@pytest.mark.asyncio
async def test_webhook_rejects_missing_idem_key(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _org_with_customer(db_session, customer_id="cus_dev_no_idem")
    event = {
        "id": "evt_no_idem",
        "type": "checkout.session.completed",
        "data": {"object": {
            "customer": "cus_dev_no_idem",
            "amount_total": 5000,
            "payment_status": "paid",
            "metadata": {},  # no topup_idem_key
        }},
    }
    resp = await client.post("/webhooks/stripe", content=json.dumps(event))
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_webhook_rejects_malformed_body(client: AsyncClient) -> None:
    resp = await client.post("/webhooks/stripe", content=b"not-json{")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_webhook_noops_when_customer_unknown(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    """A webhook for a customer we don't know about (e.g. test events
    sent before signup) is acknowledged but does nothing."""
    event = _checkout_completed_event(
        customer_id="cus_unknown_xyz",
        amount_grosze=2000,
        idem_key="idem-orphan",
    )
    resp = await client.post("/webhooks/stripe", content=json.dumps(event))
    assert resp.status_code == 200
    assert resp.json()["message"] == "noop"

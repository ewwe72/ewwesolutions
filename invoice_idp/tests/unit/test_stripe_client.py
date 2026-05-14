"""Unit tests for the Stripe client wrapper (Console implementation).

Real Stripe API is not exercised here. The Console client is just
enough to make the upper layers (signup, top-up) testable end-to-end
without network.
"""

from __future__ import annotations

import pytest

from src.app.billing.stripe_client import (
    ConsoleStripeClient,
    get_stripe_client,
)


@pytest.mark.asyncio
async def test_ensure_customer_caches_within_process() -> None:
    client = ConsoleStripeClient()
    cid_a = await client.ensure_customer(email="a@example.com", org_id="org-1")
    cid_b = await client.ensure_customer(email="a@example.com", org_id="org-1")
    assert cid_a == cid_b
    assert cid_a.startswith("cus_dev_")


@pytest.mark.asyncio
async def test_ensure_customer_different_orgs_different_ids() -> None:
    client = ConsoleStripeClient()
    cid1 = await client.ensure_customer(email="a@example.com", org_id="org-1")
    cid2 = await client.ensure_customer(email="a@example.com", org_id="org-2")
    assert cid1 != cid2


@pytest.mark.asyncio
async def test_create_topup_session_returns_url() -> None:
    client = ConsoleStripeClient()
    url = await client.create_topup_session(
        customer_id="cus_dev_test",
        amount_grosze=2000,
        success_url="http://localhost:8000/app/billing?ok=1",
        cancel_url="http://localhost:8000/app/billing?cancel=1",
        idem_key="idem-abc",
    )
    assert url.startswith("http://")
    assert "checkout" in url


def test_get_stripe_client_returns_console_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    client = get_stripe_client()
    assert isinstance(client, ConsoleStripeClient)

"""Stripe Checkout + Customer wrapper.

Two operations live here today, matching V1.3 PAYG scope:
1. `ensure_customer(email, org_id)` — get-or-create a Stripe Customer.
   Called at signup; the returned `customer_id` lives on `Org`.
2. `create_topup_session(customer_id, amount_grosze, success_url,
   cancel_url, idem_key)` — create a one-shot Stripe Checkout Session
   for adding credit. Returns the URL to redirect the user to.

Webhook handling (`/webhooks/stripe`) is wired in `src/app/api/` —
this module just gives the routes the helpers they need.

Why Checkout and not Elements / PaymentIntents directly:
- Hosted Checkout takes the PCI scope off our server entirely.
- Polish 14-day "right of withdrawal" + receipts are handled by Stripe.
- The user sees a URL they can recognise (`checkout.stripe.com`), which
  matters for accountants — they've been trained to be suspicious of
  card forms on small-vendor sites.

For dev there is a Console implementation that prints what would be
called and returns a localhost fake URL.
"""

from __future__ import annotations

import sys
import uuid
from typing import Protocol

from src.app.config import get_settings


class StripeError(RuntimeError):
    """Raised when the Stripe SDK returns an unrecoverable error."""


class StripeClient(Protocol):
    """The narrow surface Faktomat needs from Stripe."""

    async def ensure_customer(
        self, *, email: str, org_id: str,
    ) -> str: ...

    async def create_topup_session(
        self,
        *,
        customer_id: str,
        amount_grosze: int,
        success_url: str,
        cancel_url: str,
        idem_key: str,
    ) -> str: ...


class ConsoleStripeClient:
    """Dev fallback. Generates fake IDs, prints what would happen.

    `ensure_customer` returns `cus_dev_<random>` and is stable for the
    same `(email, org_id)` within a process via an internal cache —
    that mirrors prod where the customer is found by email + metadata.
    """

    def __init__(self) -> None:
        self._customers: dict[tuple[str, str], str] = {}

    async def ensure_customer(self, *, email: str, org_id: str) -> str:
        key = (email, org_id)
        cid = self._customers.get(key)
        if cid is None:
            cid = f"cus_dev_{uuid.uuid4().hex[:12]}"
            self._customers[key] = cid
            self._log(f"ensure_customer email={email} org_id={org_id} -> {cid}")
        return cid

    async def create_topup_session(
        self,
        *,
        customer_id: str,
        amount_grosze: int,
        success_url: str,
        cancel_url: str,
        idem_key: str,
    ) -> str:
        session_id = f"cs_dev_{uuid.uuid4().hex[:12]}"
        fake_url = f"http://localhost:8000/__dev/checkout/{session_id}"
        self._log(
            f"create_topup_session customer={customer_id} "
            f"amount={amount_grosze}gr idem={idem_key} -> {fake_url}"
        )
        return fake_url

    @staticmethod
    def _log(message: str) -> None:
        bar = "─" * 60
        print(f"\n{bar}\n[STRIPE-DEV] {message}\n{bar}\n",
              file=sys.stderr, flush=True)


class RealStripeClient:
    """Real Stripe SDK client. Lazily imports `stripe` so the dev path
    works without the optional dep installed."""

    def __init__(self, api_key: str) -> None:
        import stripe  # noqa: PLC0415 — optional dep, lazy

        stripe.api_key = api_key
        self._stripe = stripe

    async def ensure_customer(self, *, email: str, org_id: str) -> str:
        # Search-then-create. Stripe API search is eventual-consistency,
        # so we also filter by metadata to avoid creating duplicates
        # under concurrent signups.
        try:
            existing = self._stripe.Customer.search(
                query=f'email:"{email}" AND metadata["org_id"]:"{org_id}"',
            )
            if existing.data:
                return str(existing.data[0].id)
            created = self._stripe.Customer.create(
                email=email,
                metadata={"org_id": org_id, "app": "faktomat"},
            )
            return str(created.id)
        except Exception as e:  # noqa: BLE001 — Stripe SDK raises a wide tree
            raise StripeError(f"ensure_customer failed: {e}") from e

    async def create_topup_session(
        self,
        *,
        customer_id: str,
        amount_grosze: int,
        success_url: str,
        cancel_url: str,
        idem_key: str,
    ) -> str:
        try:
            session = self._stripe.checkout.Session.create(
                mode="payment",
                customer=customer_id,
                line_items=[{
                    "price_data": {
                        "currency": "pln",
                        "product_data": {"name": "Doładowanie Faktomat"},
                        "unit_amount": amount_grosze,
                    },
                    "quantity": 1,
                }],
                success_url=success_url,
                cancel_url=cancel_url,
                # Session-level metadata so the webhook can read
                # `topup_idem_key` straight off the event object without
                # an extra payment-intent fetch. payment_intent_data
                # metadata is duplicated for refund-path symmetry.
                metadata={"app": "faktomat", "topup_idem_key": idem_key},
                payment_intent_data={"metadata": {"app": "faktomat",
                                                   "topup_idem_key": idem_key}},
                idempotency_key=idem_key,
            )
            url = getattr(session, "url", None)
            if not url:
                raise StripeError("Stripe Checkout Session returned no URL")
            return str(url)
        except Exception as e:  # noqa: BLE001
            raise StripeError(f"create_topup_session failed: {e}") from e


def get_stripe_client() -> StripeClient:
    """Return the configured Stripe client. Real when a key is set, else console.

    The key choice is deliberately lenient: any non-empty `stripe_secret_key`
    is treated as 'real'. In dev that means setting `STRIPE_SECRET_KEY=sk_test_*`
    flips on the real client against Stripe's test mode. Empty = local console.
    """
    s = get_settings()
    if s.stripe_secret_key:
        return RealStripeClient(api_key=s.stripe_secret_key)
    return ConsoleStripeClient()

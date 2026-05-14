"""Stripe webhook endpoint — credits top-ups to `Org.credit_balance_grosze`.

Sig verification:
  * When `STRIPE_WEBHOOK_SECRET` is set, the request signature is
    verified via `stripe.Webhook.construct_event` (the SDK does
    timestamp-tolerance + HMAC-SHA256 internally).
  * When empty (dev / sandbox), the raw JSON body is trusted and a
    warning is logged. This mirrors the ConsoleStripeClient pattern —
    swap = env-var only.

Idempotency:
  Each top-up Checkout Session carries a `topup_idem_key` in its
  metadata. Before crediting, the webhook looks for an existing
  `billing.topup_credited` AuditEvent with the same `idem_key` in
  payload; if found, the request is acknowledged with 200 OK and no
  side-effects fire. Stripe retries failed deliveries with backoff,
  so duplicate deliveries are normal — replays must be inert.

Only `checkout.session.completed` is acted on for V1. Other event
types are acknowledged with 200 OK so Stripe doesn't keep retrying
them; the operator can broaden the handler later.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import get_settings
from src.app.db import get_session
from src.models.audit import AuditEvent
from src.models.org import Org

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookVerifyError(RuntimeError):
    """Raised when signature verification fails or the body is malformed."""


def _verify_and_parse(
    raw_body: bytes, sig_header: str | None, webhook_secret: str,
) -> dict[str, Any]:
    """Return the parsed event dict, or raise WebhookVerifyError.

    Real Stripe SDK is used when configured. The dev/no-secret path
    trusts the raw body — never enable that in prod (set
    `STRIPE_WEBHOOK_SECRET` to flip on real verification).
    """
    if not webhook_secret:
        logger.warning(
            "stripe webhook: STRIPE_WEBHOOK_SECRET not set — trusting body"
        )
        try:
            event = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise WebhookVerifyError(f"malformed body: {e}") from e
        if not isinstance(event, dict):
            raise WebhookVerifyError("event must be a JSON object")
        return event

    if not sig_header:
        raise WebhookVerifyError("missing Stripe-Signature header")

    try:
        import stripe  # noqa: PLC0415 — optional dep, lazy
    except ImportError as e:
        raise WebhookVerifyError(
            "stripe SDK not installed but STRIPE_WEBHOOK_SECRET is set"
        ) from e

    try:
        stripe.Webhook.construct_event(
            payload=raw_body, sig_header=sig_header, secret=webhook_secret,
        )
    except Exception as e:  # noqa: BLE001 — Stripe raises SignatureVerificationError + ValueError
        raise WebhookVerifyError(f"signature verification failed: {e}") from e
    # Signature verified — re-parse the raw body as a plain dict instead
    # of relying on the SDK's Event object (`dict(event)` fails because
    # Stripe objects implement __getitem__ for integer-indexed sequence
    # access, breaking the dict constructor's iterator path). The raw
    # body is what was signed, so we trust it after construct_event has
    # succeeded.
    try:
        result = json.loads(raw_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise WebhookVerifyError(f"verified body is not valid JSON: {e}") from e
    if not isinstance(result, dict):
        raise WebhookVerifyError("event must be a JSON object")
    return result


async def _credit_topup(
    session: AsyncSession,
    *,
    stripe_customer_id: str,
    amount_grosze: int,
    idem_key: str,
    stripe_event_id: str | None,
) -> bool:
    """Credit `amount_grosze` to the org with matching customer ID.

    Returns True if a credit was applied, False if the event was already
    processed (idempotent replay).
    """
    org = await session.scalar(
        select(Org).where(Org.stripe_customer_id == stripe_customer_id)
    )
    if org is None:
        logger.warning(
            "stripe webhook: no org for customer_id=%s (event=%s)",
            stripe_customer_id, stripe_event_id,
        )
        return False

    # Idempotency: same idem_key already credited? Audit table is the
    # source of truth — keeps webhook stateless.
    existing = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.org_id == org.id,
            AuditEvent.action == "billing.topup_credited",
            AuditEvent.payload["idem_key"].astext == idem_key,
        )
    )
    if existing is not None:
        logger.info(
            "stripe webhook: replay for idem_key=%s — skipping credit",
            idem_key,
        )
        return False

    org.credit_balance_grosze = org.credit_balance_grosze + amount_grosze
    session.add(AuditEvent(
        org_id=org.id, user_id=None, action="billing.topup_credited",
        payload={
            "idem_key": idem_key,
            "amount_grosze": amount_grosze,
            "stripe_event_id": stripe_event_id,
            "stripe_customer_id": stripe_customer_id,
        },
    ))
    await session.commit()
    return True


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> dict[str, str]:
    """Receive a Stripe event. Returns 200 once parsed (Stripe expects
    200 fast; we keep work synchronous because top-up credits are
    small and the DB write is cheap)."""
    raw_body = await request.body()
    settings = get_settings()

    try:
        event = _verify_and_parse(
            raw_body, stripe_signature, settings.stripe_webhook_secret,
        )
    except WebhookVerifyError as e:
        logger.warning("stripe webhook rejected: %s", e)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Invalid webhook",
        ) from e

    event_type = event.get("type", "")
    event_id = event.get("id")
    if event_type != "checkout.session.completed":
        # Acknowledge — don't make Stripe retry for events we ignore.
        return {"message": "ignored", "type": event_type}

    obj = event.get("data", {}).get("object", {})
    if not isinstance(obj, dict):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Malformed event.data.object",
        )

    customer_id = obj.get("customer")
    amount_total = obj.get("amount_total")
    metadata = obj.get("metadata") or {}
    idem_key = metadata.get("topup_idem_key") if isinstance(metadata, dict) else None
    payment_status = obj.get("payment_status")

    if payment_status != "paid":
        # Session can complete in 'unpaid' state for delayed-payment methods
        # (e.g. SEPA). We only credit on confirmed payment.
        return {"message": "ignored", "reason": "payment_status_not_paid"}

    if not (isinstance(customer_id, str)
            and isinstance(amount_total, int)
            and isinstance(idem_key, str)
            and amount_total > 0):
        logger.warning(
            "stripe webhook: missing required fields "
            "(customer=%r amount=%r idem=%r) — event=%s",
            customer_id, amount_total, idem_key, event_id,
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Event missing required fields for top-up credit.",
        )

    credited = await _credit_topup(
        db,
        stripe_customer_id=customer_id,
        amount_grosze=amount_total,
        idem_key=idem_key,
        stripe_event_id=event_id if isinstance(event_id, str) else None,
    )
    return {"message": "credited" if credited else "noop"}

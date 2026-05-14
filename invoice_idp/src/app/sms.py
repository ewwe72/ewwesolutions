"""SMS verification — Twilio Verify in prod, console fallback in dev.

Mirrors the `Emailer` protocol in `src/app/email.py`. `get_sms_verifier()`
returns the implementation chosen by env: Twilio when SID+TOKEN+SERVICE
are set, otherwise a console verifier that prints the code to stderr
(handy for `pytest -s` and `uvicorn --reload`).

Why a Protocol and not a class hierarchy: the auth/web routes only
need `start_verification(to)` + `check_code(to, code)`. Hiding Twilio
behind that surface keeps the integration code from leaking through
the rest of the codebase, and lets the worker / signup flow swap
implementations via Settings without import cycles.
"""

from __future__ import annotations

import sys
from typing import Protocol

import httpx

from src.app.config import get_settings


class VerifyError(RuntimeError):
    """Raised when the verification provider returns a non-recoverable error."""


class SmsVerifier(Protocol):
    """Implementations send a one-time code to a phone number and validate it.

    The provider stores the code/status server-side, so the caller never
    sees the secret — that's why `check_code` returns bool, not "is this
    the code I stored". Avoids reinventing rate-limiting + brute-force
    protections on our side.
    """

    async def start_verification(self, phone_e164: str) -> None: ...

    async def check_code(self, phone_e164: str, code: str) -> bool: ...


class ConsoleSmsVerifier:
    """Dev fallback. Code is the last 6 chars of the phone number — totally
    insecure, totally fine for local. Prints what was 'sent' to stderr."""

    async def start_verification(self, phone_e164: str) -> None:
        code = _dev_code_for(phone_e164)
        bar = "─" * 60
        print(
            f"\n{bar}\n[SMS → {phone_e164}]\nKod weryfikacyjny: {code}\n{bar}\n",
            file=sys.stderr,
            flush=True,
        )

    async def check_code(self, phone_e164: str, code: str) -> bool:
        return code == _dev_code_for(phone_e164)


def _dev_code_for(phone_e164: str) -> str:
    """Deterministic dev code so tests can predict the value without
    capturing stderr. Last 6 digits of the phone, padded if shorter."""
    digits = "".join(c for c in phone_e164 if c.isdigit())
    return (digits or "000000")[-6:].rjust(6, "0")


class TwilioSmsVerifier:
    """Real Twilio Verify v2 client.

    Uses the Verify Service workflow (a Service SID groups verifications
    so quota, rate-limit and rotated codes live on Twilio's side). We
    intentionally do not fall back to raw SMS APIs — Verify gives us
    brute-force protection + multi-channel for free.
    """

    BASE_URL = "https://verify.twilio.com/v2/Services"

    def __init__(self, account_sid: str, auth_token: str, service_sid: str) -> None:
        self._auth = (account_sid, auth_token)
        self._service_sid = service_sid

    async def start_verification(self, phone_e164: str) -> None:
        url = f"{self.BASE_URL}/{self._service_sid}/Verifications"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                data={"To": phone_e164, "Channel": "sms"},
                auth=self._auth,
            )
        if resp.status_code >= 400:
            raise VerifyError(
                f"Twilio start_verification {resp.status_code}: {resp.text}"
            )

    async def check_code(self, phone_e164: str, code: str) -> bool:
        url = f"{self.BASE_URL}/{self._service_sid}/VerificationCheck"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                data={"To": phone_e164, "Code": code},
                auth=self._auth,
            )
        # Twilio returns 200 with `status=approved` on match, `status=pending`
        # on mismatch, 404 on expired/exhausted attempts.
        if resp.status_code == 404:
            return False
        if resp.status_code >= 400:
            raise VerifyError(
                f"Twilio check_code {resp.status_code}: {resp.text}"
            )
        body = resp.json()
        return bool(body.get("status") == "approved")


def get_sms_verifier() -> SmsVerifier:
    """Return the configured verifier — Twilio when fully configured, else console."""
    s = get_settings()
    if s.twilio_account_sid and s.twilio_auth_token and s.twilio_verify_service_sid:
        return TwilioSmsVerifier(
            account_sid=s.twilio_account_sid,
            auth_token=s.twilio_auth_token,
            service_sid=s.twilio_verify_service_sid,
        )
    return ConsoleSmsVerifier()

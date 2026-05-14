"""Transactional email — Postmark in prod, console fallback in dev.

`get_emailer()` returns whichever implementation is configured. When
`POSTMARK_API_TOKEN` + `POSTMARK_FROM_EMAIL` are both set in .env,
real Postmark API calls happen. Otherwise emails are pretty-printed
to stderr so the developer can copy the verification link manually.
"""

from __future__ import annotations

import sys
from typing import Protocol

import httpx

from src.app.config import get_settings

POSTMARK_API_URL = "https://api.postmarkapp.com/email"


class Emailer(Protocol):
    """Implementations send a single transactional email."""

    async def send(
        self,
        to: str,
        subject: str,
        text: str,
        html: str | None = None,
    ) -> None: ...


class ConsoleEmailer:
    """Dev fallback — prints email contents to stderr."""

    async def send(
        self,
        to: str,
        subject: str,
        text: str,
        html: str | None = None,
    ) -> None:
        bar = "─" * 60
        print(f"\n{bar}\n[EMAIL → {to}]\nSubject: {subject}\n{bar}\n{text}\n{bar}\n",
              file=sys.stderr, flush=True)


class PostmarkEmailer:
    """Real Postmark client. Uses the Server API token authorisation header."""

    def __init__(self, api_token: str, from_email: str) -> None:
        self._api_token = api_token
        self._from_email = from_email

    async def send(
        self,
        to: str,
        subject: str,
        text: str,
        html: str | None = None,
    ) -> None:
        payload: dict[str, str] = {
            "From": self._from_email,
            "To": to,
            "Subject": subject,
            "TextBody": text,
            "MessageStream": "outbound",
        }
        if html is not None:
            payload["HtmlBody"] = html

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                POSTMARK_API_URL,
                json=payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Postmark-Server-Token": self._api_token,
                },
            )
        response.raise_for_status()


def get_emailer() -> Emailer:
    """Return the configured emailer — Postmark if both creds set, else console."""
    settings = get_settings()
    if settings.postmark_api_token and settings.postmark_from_email:
        return PostmarkEmailer(
            api_token=settings.postmark_api_token,
            from_email=settings.postmark_from_email,
        )
    return ConsoleEmailer()

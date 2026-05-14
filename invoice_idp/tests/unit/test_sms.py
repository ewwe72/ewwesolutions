"""Unit tests for the SMS verifier protocol.

Real Twilio Verify isn't exercised here — that's an integration test
for a session running with the live keys. The ConsoleSmsVerifier is
deterministic so we can verify the contract round-trip.
"""

from __future__ import annotations

import pytest

from src.app.sms import ConsoleSmsVerifier, _dev_code_for, get_sms_verifier


@pytest.mark.asyncio
async def test_console_verifier_round_trip() -> None:
    sv = ConsoleSmsVerifier()
    await sv.start_verification("+48600100200")
    assert await sv.check_code("+48600100200", "100200") is True


@pytest.mark.asyncio
async def test_console_verifier_rejects_wrong_code() -> None:
    sv = ConsoleSmsVerifier()
    await sv.start_verification("+48600100200")
    assert await sv.check_code("+48600100200", "999999") is False


@pytest.mark.asyncio
async def test_console_verifier_code_is_phone_suffix() -> None:
    # The dev code is intentionally derivable from the phone so tests
    # don't have to scrape stderr. Document the rule here.
    assert _dev_code_for("+48600100200") == "100200"
    assert _dev_code_for("+12025550199") == "550199"
    # Short / weird inputs degrade gracefully.
    assert _dev_code_for("+48") == "000048"
    assert _dev_code_for("") == "000000"


def test_get_sms_verifier_returns_console_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "")
    monkeypatch.setenv("TWILIO_VERIFY_SERVICE_SID", "")
    verifier = get_sms_verifier()
    assert isinstance(verifier, ConsoleSmsVerifier)

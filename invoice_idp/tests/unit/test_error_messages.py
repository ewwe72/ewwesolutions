"""Unit coverage for `friendly_extraction_error` — the mapping from
raw worker exceptions to user-facing Polish messages.

The function classifies by exception class name and message substrings.
We exercise each branch with a synthesised exception so the categories
stay stable across refactors. If a paying user starts seeing a leaked
Python traceback in the UI, this is the test that should catch it.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from src.app.workers.error_messages import (
    _GENERIC,
    friendly_extraction_error,
)


# Helper to mint an exception with a given class name and message
# without importing every SDK error class (boto3 / anthropic). The
# classifier reads `type(exc).__name__` and `str(exc)`, both of which
# we can fake with dynamically-named subclasses.
def _fake_exc(class_name: str, message: str = "") -> Exception:
    cls = type(class_name, (Exception,), {})
    return cls(message)


# ── PDF input ─────────────────────────────────────────────────────────

def test_too_many_pages_returns_split_advice() -> None:
    exc = ValueError("invoice.pdf: 12 pages > max 10")
    msg = friendly_extraction_error(exc)
    assert "za dużo stron" in msg
    assert "10" in msg
    assert "Podziel" in msg


# ── Storage errors ────────────────────────────────────────────────────

def test_endpoint_connection_error_is_storage_retry_message() -> None:
    exc = _fake_exc("EndpointConnectionError", "Could not connect to the endpoint URL")
    msg = friendly_extraction_error(exc)
    assert "dostępem do pliku" in msg
    assert "Spróbuj ponownie" in msg


def test_no_such_key_tells_user_to_reupload() -> None:
    exc = _fake_exc("NoSuchKey", "The specified key does not exist.")
    msg = friendly_extraction_error(exc)
    assert "nie został znaleziony" in msg
    assert "Wgraj go ponownie" in msg


# ── Rate limit / throttle ─────────────────────────────────────────────

@pytest.mark.parametrize("cls", [
    "ThrottlingException", "RateLimitError",
    "OverloadedError", "TooManyRequestsException",
])
def test_throttle_classes_map_to_overload_message(cls: str) -> None:
    msg = friendly_extraction_error(_fake_exc(cls, "rate limited"))
    assert "przeciążony" in msg
    assert "Spróbuj ponownie" in msg


def test_throttle_caught_via_message_substring() -> None:
    # Some SDKs raise a generic ClientError with the detail in the message.
    exc = _fake_exc("ClientError", "An error occurred (ThrottlingException) when calling InvokeModel")
    msg = friendly_extraction_error(exc)
    assert "przeciążony" in msg


# ── Auth / permission ─────────────────────────────────────────────────

@pytest.mark.parametrize("cls", [
    "AccessDeniedException", "PermissionDeniedError",
    "AuthenticationError", "NotAuthorizedException",
])
def test_auth_errors_route_user_to_admin(cls: str) -> None:
    msg = friendly_extraction_error(_fake_exc(cls, "blocked"))
    assert "Brak dostępu" in msg
    assert "administratorem" in msg


# ── Backend down ──────────────────────────────────────────────────────

@pytest.mark.parametrize("cls", [
    "ServiceUnavailableException", "InternalServerException",
    "InternalServerError", "APIConnectionError",
])
def test_backend_classes_map_to_unavailable_message(cls: str) -> None:
    msg = friendly_extraction_error(_fake_exc(cls, "5xx"))
    assert "niedostępny" in msg
    assert "kilka minut" in msg


# ── Timeouts ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("cls", [
    "TimeoutError", "APITimeoutError", "ModelTimeoutException", "ReadTimeoutError",
])
def test_timeout_classes_map_to_timeout_message(cls: str) -> None:
    msg = friendly_extraction_error(_fake_exc(cls, "deadline exceeded"))
    assert "limit czasu" in msg


# ── Pydantic ValidationError ──────────────────────────────────────────

def test_pydantic_validation_error_points_to_sonnet_button() -> None:
    class _M(BaseModel):
        x: int

    with pytest.raises(ValidationError) as excinfo:
        _M.model_validate({"x": "not-an-int"})

    msg = friendly_extraction_error(excinfo.value)
    assert "niespójne dane" in msg
    assert "Sonnet" in msg


# ── Tool-use / parse failure (RuntimeError from the pipeline) ─────────

def test_haiku_parse_failure_points_to_sonnet_button() -> None:
    exc = RuntimeError(
        "Haiku failed to parse a valid invoice from test.pdf; "
        "operator may retry with Sonnet via the review page."
    )
    msg = friendly_extraction_error(exc)
    assert "Sonnet" in msg
    assert "Spróbuj ponownie" in msg


def test_model_did_not_call_tool_points_to_sonnet_button() -> None:
    exc = RuntimeError(
        "Model claude-haiku-4-5 did not call emit_invoice; stop_reason=end_turn"
    )
    msg = friendly_extraction_error(exc)
    assert "Sonnet" in msg


# ── Generic fallback ──────────────────────────────────────────────────

def test_unknown_exception_falls_through_to_generic_polish_message() -> None:
    exc = _fake_exc("SomeWeirdInternalError", "blah blah whatever")
    msg = friendly_extraction_error(exc)
    assert msg == _GENERIC
    # Generic message must never leak the Python class name.
    assert "SomeWeirdInternalError" not in msg
    assert "Exception" not in msg


def test_runtime_error_with_no_known_substring_falls_through() -> None:
    exc = RuntimeError("something else broke")
    assert friendly_extraction_error(exc) == _GENERIC


# ── Guarantees about every category ───────────────────────────────────

_REPRESENTATIVE_EXCEPTIONS: list[BaseException] = [
    ValueError("doc.pdf: 11 pages > max 10"),
    _fake_exc("EndpointConnectionError"),
    _fake_exc("NoSuchKey"),
    _fake_exc("ThrottlingException"),
    _fake_exc("AccessDeniedException"),
    _fake_exc("ServiceUnavailableException"),
    _fake_exc("TimeoutError"),
    RuntimeError("did not call emit_invoice"),
    _fake_exc("CompletelyUnknownClass"),
]


@pytest.mark.parametrize("exc", _REPRESENTATIVE_EXCEPTIONS)
def test_every_branch_returns_polish_actionable_message(exc: BaseException) -> None:
    """No branch should leak a Python class name or English-only text
    into a paying user's UI. Each message must be Polish and end with
    a soft call-to-action ("Spróbuj ponownie", "Skontaktuj się…",
    "Podziel…", "Wgraj…", or the generic fallback's longer copy)."""
    msg = friendly_extraction_error(exc)
    # No Python class names leak.
    assert type(exc).__name__ not in msg, f"class name leaked: {msg!r}"
    # Empty / placeholder return values would be a regression.
    assert msg.strip(), "empty message"
    # Polish stem checks: at least one Polish-only marker present.
    polish_markers = ("Spróbuj", "Wgraj", "Podziel", "Skontaktuj", "Wystąpił")
    assert any(m in msg for m in polish_markers), msg

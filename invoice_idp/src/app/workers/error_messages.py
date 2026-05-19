"""Map raw extraction exceptions to user-facing Polish messages.

The worker stores the friendly message in `invoice.extraction_error`,
which is what the user sees on `invoice_detail_stub.html`. The raw
technical detail (Python class name + message) lives in the audit event
under `payload.error_raw`, so ops debugging through the audit log still
has the exact exception text.

Categories are matched by exception class name and substring, ordered
most-specific to most-general. Anything unmatched falls through to a
generic Polish message so the user never sees a Python traceback or
boto3 internals.

The point of this module is *not* to be exhaustive — boto3 alone has
dozens of exception classes. It is to cover the cases that actually
recur in production and to keep the failure surface readable for a
Polish accountant who has no reason to know what `ClientError` is.
"""

from __future__ import annotations


_GENERIC = (
    "Wystąpił nieoczekiwany błąd podczas ekstrakcji. "
    "Spróbuj ponownie lub skontaktuj się z administratorem, "
    "jeśli problem się powtarza."
)


def friendly_extraction_error(exc: BaseException) -> str:
    """Return a Polish user-facing message for an extraction failure.

    The first matching branch wins. Branches are ordered most-specific
    first (e.g. a `ValueError` with `pages >` in the message is caught
    before the generic Pydantic-ValidationError branch).
    """
    cls = type(exc).__name__
    msg = str(exc)
    msg_lower = msg.lower()

    # ── PDF input the user can fix by re-uploading ────────────────────
    if cls == "ValueError" and "pages >" in msg_lower:
        return (
            "PDF ma za dużo stron (limit: 10). "
            "Podziel dokument i wgraj każdy plik osobno."
        )

    # ── Storage / object-store problems ───────────────────────────────
    if cls in {"EndpointConnectionError", "ConnectTimeoutError"} or (
        "could not connect to the endpoint" in msg_lower
    ):
        return (
            "Problem z dostępem do pliku PDF. "
            "Spróbuj ponownie za chwilę."
        )
    if cls == "NoSuchKey" or "the specified key does not exist" in msg_lower:
        return (
            "Plik PDF nie został znaleziony w magazynie. "
            "Wgraj go ponownie."
        )

    # ── Rate limit / throttle (transient, retry-friendly) ─────────────
    if cls in {
        "ThrottlingException",
        "RateLimitError",
        "OverloadedError",
        "TooManyRequestsException",
    } or (
        "throttl" in msg_lower
        or "rate limit" in msg_lower
        or "overload" in msg_lower
    ):
        return (
            "Model AI jest chwilowo przeciążony. "
            "Spróbuj ponownie za chwilę."
        )

    # ── Auth / permission (operator action needed) ────────────────────
    if cls in {
        "AccessDeniedException",
        "PermissionDeniedError",
        "AuthenticationError",
        "UnauthorizedException",
        "NotAuthorizedException",
    } or (
        "access denied" in msg_lower
        or "not authorized" in msg_lower
        or "permission" in msg_lower and "denied" in msg_lower
    ):
        return (
            "Brak dostępu do modelu AI. "
            "Skontaktuj się z administratorem."
        )

    # ── Bedrock / Anthropic backend issues ────────────────────────────
    if cls in {
        "ServiceUnavailableException",
        "InternalServerException",
        "InternalServerError",
        "ServiceUnavailableError",
        "APIConnectionError",
    } or "service unavailable" in msg_lower:
        return (
            "Model AI jest chwilowo niedostępny. "
            "Spróbuj ponownie za kilka minut."
        )

    # ── Timeouts ──────────────────────────────────────────────────────
    if cls in {
        "TimeoutError",
        "APITimeoutError",
        "ModelTimeoutException",
        "ReadTimeoutError",
    }:
        return (
            "Ekstrakcja przekroczyła limit czasu. "
            "Spróbuj ponownie."
        )

    # ── Pydantic schema mismatch on the model's output ────────────────
    if cls == "ValidationError":
        return (
            "Model AI zwrócił niespójne dane. "
            "Spróbuj ponownie — przyciskiem \"Spróbuj ponownie (Sonnet)\" "
            "uruchomisz mocniejszy model."
        )

    # ── Model misbehavior: didn't call tool, or returned unparseable ──
    if "did not call" in msg_lower or "failed to parse" in msg_lower:
        return (
            "Model AI nie zwrócił poprawnej odpowiedzi. "
            "Spróbuj ponownie — przyciskiem \"Spróbuj ponownie (Sonnet)\" "
            "uruchomisz mocniejszy model."
        )

    return _GENERIC

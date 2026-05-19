"""Upload validation: POST /app/wgraj rejects malformed file submissions
*after* the email+balance gate passes but *before* anything touches
storage or the worker queue.

Four guard branches in `src/app/web/routes.py::upload_submit`:

  1. Missing `pdf` form field        → 400 "Brak pliku."
  2. Empty file (0 bytes)            → 400 "Plik jest pusty."
  3. Oversized file (> max_upload_mb) → 400 "Plik większy niż N MB."
  4. Not a PDF (no `%PDF-` magic)    → 400 "To nie jest PDF (brak nagłówka pliku PDF)."

These complement the gate tests (`test_upload_gate.py`) and the
client-side JS guard in `upload.html`. The server-side branches matter
because the JS guard can be bypassed (curl, disabled JS, scripted
abuse) — without these checks an attacker could push 500 MB junk
straight to MinIO and burn the next extraction debit on garbage.

All four branches bail with `TemplateResponse` *before* `storage.put`
or `_enqueue_extraction`, so these tests do not need MinIO or arq
queue activity to be functional — they just assert the 400 + error
copy + that no Invoice row is created.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.invoice_record import Invoice
from src.models.org import Org
from src.models.user import User


async def _signup_login_with_balance(
    client: AsyncClient, db_session: AsyncSession, email: str,
    *, balance_grosze: int = 5000,
) -> User:
    """Create a user, verify email, grant balance, and log in.

    This shortcut puts the user *past* the upload gate so we can
    exercise the file-validation branches directly. Without it, every
    test below would redirect to /app?reason=email_verify and we'd
    test the gate instead of the validation.
    """
    await client.post(
        "/auth/signup",
        json={"email": email, "password": "supersecret123"},
    )
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    user.email_verified = True
    user.email_verification_token = None
    org = await db_session.scalar(select(Org).where(Org.id == user.org_id))
    assert org is not None
    org.credit_balance_grosze = balance_grosze
    await db_session.commit()
    await client.post(
        "/auth/login",
        json={"email": email, "password": "supersecret123"},
    )
    return user


async def _count_invoices(db_session: AsyncSession, org_id: object) -> int:
    """No Invoice row should be persisted on any validation failure."""
    result = await db_session.scalar(
        select(func.count()).select_from(Invoice).where(Invoice.org_id == org_id)
    )
    return int(result or 0)


@pytest.mark.asyncio
async def test_upload_post_rejects_missing_pdf_field(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    """No `pdf` form field at all (e.g. curl `-F other=...`) → 400 with
    Polish copy, no DB write. Catches the `hasattr(upload, "read")`
    branch at routes.py:527."""
    user = await _signup_login_with_balance(client, db_session, "noupload@example.com")
    # Send a multipart with a *different* field name so FastAPI's form()
    # has a body to parse but `form.get("pdf")` returns None.
    resp = await client.post(
        "/app/wgraj",
        files={"not_pdf": ("a.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert resp.status_code == 400, resp.text
    assert "Brak pliku." in resp.text
    # Upload form re-rendered so the user can retry.
    assert 'name="pdf"' in resp.text
    assert await _count_invoices(db_session, user.org_id) == 0


@pytest.mark.asyncio
async def test_upload_post_rejects_empty_file(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    """Zero-byte file → 400 "Plik jest pusty." Catches routes.py:539."""
    user = await _signup_login_with_balance(client, db_session, "empty@example.com")
    resp = await client.post(
        "/app/wgraj",
        files={"pdf": ("empty.pdf", b"", "application/pdf")},
    )
    assert resp.status_code == 400, resp.text
    assert "Plik jest pusty." in resp.text
    assert await _count_invoices(db_session, user.org_id) == 0


@pytest.mark.asyncio
async def test_upload_post_rejects_oversized_file(
    client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """File larger than `max_upload_mb` → 400 with the configured cap
    spelled out in Polish. Catches routes.py:541-542.

    Patches `get_settings` to a 1 MB cap so we can trip the branch with
    a small payload (avoid 20 MB test fixtures). The error message must
    quote the *patched* cap, not the default 20, proving the route
    reads `settings.max_upload_mb` at request time (not at import).
    """
    from src.app.web import routes as web_routes

    real_settings = web_routes.get_settings()
    monkeypatch.setattr(
        web_routes,
        "get_settings",
        lambda: real_settings.model_copy(update={"max_upload_mb": 1}),
    )

    user = await _signup_login_with_balance(client, db_session, "oversize@example.com")
    # 1.5 MB of PDF-prefixed bytes: starts with magic so we exercise
    # the size branch (line 541) specifically, not the magic branch.
    payload = b"%PDF-1.4\n" + b"X" * (int(1.5 * 1024 * 1024))
    resp = await client.post(
        "/app/wgraj",
        files={"pdf": ("big.pdf", payload, "application/pdf")},
    )
    assert resp.status_code == 400, resp.text
    assert "Plik większy niż 1 MB." in resp.text, (
        "expected '1 MB' (patched cap) in error; got default fallback "
        "or different copy"
    )
    assert await _count_invoices(db_session, user.org_id) == 0


@pytest.mark.asyncio
async def test_upload_post_rejects_non_pdf_content(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    """Non-empty content without `%PDF-` magic prefix → 400. Catches
    routes.py:543. Sends a real-looking JPEG header so the only thing
    failing the check is the magic-bytes assertion, not size or
    emptiness."""
    user = await _signup_login_with_balance(client, db_session, "notpdf@example.com")
    jpeg_header = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00"
    resp = await client.post(
        "/app/wgraj",
        files={"pdf": ("photo.jpg", jpeg_header, "application/pdf")},
    )
    assert resp.status_code == 400, resp.text
    assert "To nie jest PDF" in resp.text
    assert "nagłówka pliku PDF" in resp.text
    assert await _count_invoices(db_session, user.org_id) == 0


# ---------------------------------------------------------------------------
# Web-form happy-path coverage (GET render + POST success)
#
# The four tests above target the validation guard branches. The two
# below close out the form's happy-path so we have coverage of:
#   - GET /app/wgraj rendering (template context wiring: max_upload_mb,
#     CSRF session token, form HTML present)
#   - POST /app/wgraj success path (Invoice row created, redirect to
#     detail page)
#
# Storage + arq enqueue are monkeypatched out so we don't need MinIO or
# Redis running. The web POST handler does `from src.app.storage import
# get_storage` and `from src.app.api.invoices import _enqueue_extraction`
# inside the function body — so we patch the source modules, not the
# `web.routes` namespace.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_upload_form_renders_with_context(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    """GET /app/wgraj as an authenticated, verified, balance-positive
    user returns the upload form with `max_upload_mb` threaded into the
    template and a CSRF token established in the session.

    The upload form itself deliberately omits a hidden `csrf_token`
    input — POST /app/wgraj does not call `_verify_csrf_form` (see
    routes.py:498-604). This is consistent with the JSON API surface
    `/api/v1/invoices` which also accepts unauthenticated-by-CSRF
    multipart uploads from the same session. CSRF *is* generated and
    stored in the session on this GET (routes.py:487), which we verify
    by checking the session cookie was issued / refreshed; the token
    itself isn't echoed into the page.
    """
    from src.app.config import get_settings

    await _signup_login_with_balance(client, db_session, "uploader-render@example.com")
    resp = await client.get("/app/wgraj")
    assert resp.status_code == 200, resp.text

    body = resp.text
    # Form is rendered (action + file input + submit label from upload.html).
    assert 'action="/app/wgraj"' in body
    assert 'name="pdf"' in body
    assert "Wgraj i ekstraktuj" in body  # unique submit-button label

    # `max_upload_mb` from settings is threaded into both the visible
    # copy ("maks. N MB") and the JS guard (`MAX_UPLOAD_MB = N;`).
    max_mb = get_settings().max_upload_mb
    assert f"maks. {max_mb} MB" in body
    assert f"MAX_UPLOAD_MB = {max_mb};" in body

    # CSRF token was generated for the session (routes.py:487 calls
    # `get_or_create_csrf_token`, which writes it into the session).
    # Starlette's SessionMiddleware only re-sets the cookie when the
    # session is mutated; by the time we hit /app/wgraj the token has
    # usually already been written by a prior handler (login flow),
    # so the cookie may not appear on *this* response. What we *can*
    # assert is that the AsyncClient has a session cookie at all,
    # proving the session round-tripped and CSRF could be persisted.
    cookie_names = {c.lower() for c in client.cookies.keys()}
    assert any("session" in n for n in cookie_names), (
        f"expected a session cookie on the client; got {cookie_names!r}"
    )


@pytest.mark.asyncio
async def test_web_upload_form_accepts_valid_pdf(
    client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /app/wgraj with a valid minimal PDF redirects (303) to the
    invoice detail page and creates exactly one pending Invoice row for
    the org. Mirrors the JSON-API happy path in
    test_invoices.py::test_upload_creates_invoice_and_enqueues_job.

    Storage + arq are stubbed out — this test asserts the *route's*
    happy path, not S3 or Redis liveness.
    """
    from uuid import UUID

    from src.app.api import invoices as invoices_module
    from src.app import storage as storage_module
    from src.models.invoice_record import Invoice

    puts: list[tuple[str, bytes]] = []
    enqueued: list[str] = []

    class _FakeStorage:
        def put(
            self, key: str, content: bytes, content_type: str = "application/pdf",
        ) -> None:
            puts.append((key, content))

    async def _fake_enqueue(invoice_id: UUID) -> None:
        enqueued.append(str(invoice_id))

    # Web route does `from src.app.storage import get_storage` and
    # `from src.app.api.invoices import _enqueue_extraction` at call
    # time, so patching the source modules is enough.
    monkeypatch.setattr(storage_module, "get_storage", lambda: _FakeStorage())
    monkeypatch.setattr(invoices_module, "_enqueue_extraction", _fake_enqueue)

    user = await _signup_login_with_balance(
        client, db_session, "web-uploader@example.com",
    )

    min_pdf = b"%PDF-1.4\n%fake\n%%EOF\n"
    resp = await client.post(
        "/app/wgraj",
        files={"pdf": ("test.pdf", min_pdf, "application/pdf")},
    )

    # Success path is a 303 redirect to /app/faktury/{invoice_id}
    # (routes.py:601-604). httpx's AsyncClient doesn't auto-follow
    # redirects unless `follow_redirects=True` is passed.
    assert resp.status_code == 303, resp.text
    location = resp.headers["location"]
    assert location.startswith("/app/faktury/"), location

    # Exactly one Invoice row exists for the org, in `pending` state,
    # and its id matches the redirect target.
    invoice = await db_session.scalar(
        select(Invoice).where(Invoice.org_id == user.org_id)
    )
    assert invoice is not None
    assert invoice.status == "pending"
    assert invoice.original_filename == "test.pdf"
    assert invoice.pdf_size_bytes == len(min_pdf)
    assert location == f"/app/faktury/{invoice.id}"

    # Storage saw the bytes, arq saw the enqueue. Neither is the
    # primary assertion here, but a quiet stub list is a useful tripwire
    # for "the route bailed before side-effects" regressions.
    assert len(puts) == 1
    assert puts[0][1] == min_pdf
    assert enqueued == [str(invoice.id)]

    # Negative-control: success path should NOT re-render the upload
    # form (which it does on every validation error). A 303 carries no
    # body, but verify there's no error-template marker just in case
    # someone later swaps the redirect for an inline TemplateResponse.
    assert "Wgraj i ekstraktuj" not in resp.text
    assert "Brak pliku." not in resp.text

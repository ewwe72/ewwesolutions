"""Web (HTML) routes — Jinja templates + traditional form submits.

Mirrors the auth flow that `src/app/auth/routes.py` exposes as JSON.
Both routers share the underlying SQLAlchemy models and password
helpers; the duplication here is light enough to leave as-is for V1.
Refactor to a shared service layer when a third client type appears.

CSRF on web routes: hidden `csrf_token` form field, validated inline
against the session-stored token. The auth JSON router uses the
`X-CSRF-Token` header pattern; both check the same session key.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.auth.csrf import CSRF_SESSION_KEY, get_or_create_csrf_token
from src.app.auth.passwords import hash_password, verify_password
from src.app.config import get_settings
from src.app.db import get_session
from src.models.audit import AuditEvent
from src.models.invoice import CanonicalInvoice
from src.models.invoice_record import Invoice
from src.models.org import Org
from src.models.user import User
from src.app.billing.stripe_client import StripeError, get_stripe_client
from src.pipeline.validation.checks import validate as validate_invoice
from src.utils.kod_urzedu import is_valid_kod_urzedu, normalize_kod_urzedu
from src.utils.nip import is_valid_nip, normalize_nip
from src.utils.regon import is_valid_regon, normalize_regon

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["web"])

VERIFICATION_TOKEN_TTL_HOURS = 24
PASSWORD_RESET_TTL_MINUTES = 15


async def _get_user_or_none(
    request: Request, session: AsyncSession
) -> User | None:
    raw = request.session.get("user_id")
    if not raw:
        return None
    try:
        uid = UUID(raw)
    except (ValueError, TypeError):
        request.session.clear()
        return None
    user: User | None = await session.scalar(
        select(User).where(User.id == uid, User.deleted_at.is_(None))
    )
    return user


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _verify_csrf_form(request: Request, token: str) -> None:
    session_token = request.session.get(CSRF_SESSION_KEY)
    if not session_token or not secrets.compare_digest(session_token, token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token mismatch")


async def _send_email_safe(coro_func: Any, *args: Any) -> None:
    """Wrap email send — never fail the request because the email provider is down."""
    try:
        await coro_func(*args)
    except Exception as e:  # noqa: BLE001
        logger.warning("email send failed: %s", e)


# ── Public pages ──────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request) -> Response:
    return templates.TemplateResponse(request, "index.html", {})


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    return templates.TemplateResponse(request, "auth/login.html", {})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    user = await session.scalar(
        select(User).where(User.email == email, User.deleted_at.is_(None))
    )
    if user is None or not verify_password(password, user.password_hash):
        session.add(AuditEvent(
            user_id=user.id if user else None,
            action="auth.login_failed",
            payload={"email": email, "via": "web"},
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ))
        await session.commit()
        return templates.TemplateResponse(
            request, "auth/login.html",
            {"error": "Niepoprawny email lub hasło.", "email": email},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user.last_login_at = datetime.now(timezone.utc)
    request.session["user_id"] = str(user.id)
    get_or_create_csrf_token(request)
    session.add(AuditEvent(
        org_id=user.org_id, user_id=user.id, action="auth.login",
        payload={"via": "web"}, ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    ))
    await session.commit()
    return RedirectResponse(url="/app", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request) -> Response:
    return templates.TemplateResponse(request, "auth/signup.html", {})


@router.post("/signup", response_class=HTMLResponse)
async def signup_submit(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form(min_length=8, max_length=128)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        return templates.TemplateResponse(
            request, "auth/signup.html",
            {"error": "Konto z tym adresem już istnieje.", "email": email},
            status_code=status.HTTP_409_CONFLICT,
        )

    org = Org(name=email)
    session.add(org)
    await session.flush()

    user = User(
        email=email,
        password_hash=hash_password(password),
        org_id=org.id,
        email_verification_token=secrets.token_urlsafe(48),
        email_verification_sent_at=datetime.now(timezone.utc),
    )
    session.add(user)
    session.add(AuditEvent(
        org_id=org.id, user_id=user.id, action="auth.signup",
        payload={"email": email, "via": "web"},
        ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
    ))
    await session.commit()
    await session.refresh(user)

    # Send verification email (lazy import keeps auth-json routes from
    # taking the dependency unnecessarily during cold start).
    from src.app.auth.routes import _send_verification_email
    await _send_email_safe(_send_verification_email, user)

    return templates.TemplateResponse(
        request, "auth/signup_done.html",
        {"email": email}, status_code=status.HTTP_201_CREATED,
    )


@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email_page(
    request: Request,
    token: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    user = await session.scalar(
        select(User).where(User.email_verification_token == token)
    )
    if user is None:
        return templates.TemplateResponse(
            request, "auth/verify_result.html",
            {"ok": False, "message": "Link niepoprawny lub już użyty."},
        )

    if user.email_verification_sent_at is not None:
        expiry = user.email_verification_sent_at + timedelta(hours=VERIFICATION_TOKEN_TTL_HOURS)
        if expiry < datetime.now(timezone.utc):
            return templates.TemplateResponse(
                request, "auth/verify_result.html",
                {"ok": False, "message": "Link wygasł — załóż konto ponownie."},
            )

    user.email_verified = True
    user.email_verification_token = None
    user.email_verification_sent_at = None
    session.add(AuditEvent(
        org_id=user.org_id, user_id=user.id, action="auth.email_verified",
        payload={"via": "web"},
    ))
    await session.commit()
    return templates.TemplateResponse(request, "auth/verify_result.html", {"ok": True})


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_page(request: Request) -> Response:
    return templates.TemplateResponse(request, "auth/forgot.html", {"sent": False})


@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot_submit(
    request: Request,
    email: Annotated[str, Form()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    user = await session.scalar(
        select(User).where(User.email == email, User.deleted_at.is_(None))
    )
    if user is not None:
        user.password_reset_token = secrets.token_urlsafe(48)
        user.password_reset_expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_TTL_MINUTES)
        )
        session.add(AuditEvent(
            org_id=user.org_id, user_id=user.id,
            action="auth.password_reset_requested",
            payload={"via": "web"}, ip=_client_ip(request),
        ))
        await session.commit()
        from src.app.auth.routes import _send_password_reset_email
        await _send_email_safe(_send_password_reset_email, user)

    # Always the same response — no email enumeration.
    return templates.TemplateResponse(
        request, "auth/forgot.html", {"sent": True},
    )


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_page(request: Request, token: str) -> Response:
    return templates.TemplateResponse(
        request, "auth/reset.html", {"token": token, "done": False},
    )


@router.post("/reset-password", response_class=HTMLResponse)
async def reset_submit(
    request: Request,
    token: Annotated[str, Form()],
    password: Annotated[str, Form(min_length=8, max_length=128)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    user = await session.scalar(
        select(User).where(
            User.password_reset_token == token,
            User.deleted_at.is_(None),
        )
    )
    if user is None or user.password_reset_expires_at is None:
        return templates.TemplateResponse(
            request, "auth/reset.html",
            {"token": token, "done": False, "error": "Link niepoprawny lub już użyty."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if user.password_reset_expires_at < datetime.now(timezone.utc):
        return templates.TemplateResponse(
            request, "auth/reset.html",
            {"token": token, "done": False, "error": "Link wygasł — poproś o nowy."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user.password_hash = hash_password(password)
    user.password_reset_token = None
    user.password_reset_expires_at = None
    session.add(AuditEvent(
        org_id=user.org_id, user_id=user.id, action="auth.password_reset",
        payload={"via": "web"}, ip=_client_ip(request),
    ))
    await session.commit()
    return templates.TemplateResponse(
        request, "auth/reset.html", {"done": True},
    )


@router.post("/logout")
async def web_logout(
    request: Request,
    csrf_token: Annotated[str, Form()],
) -> Response:
    _verify_csrf_form(request, csrf_token)
    request.session.clear()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


# ── Authenticated app pages ───────────────────────────────────────────────


@router.get("/app", response_class=HTMLResponse)
async def app_dashboard(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    user = await _get_user_or_none(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    invoices = list(await session.scalars(
        select(Invoice)
        .where(Invoice.org_id == user.org_id, Invoice.deleted_at.is_(None))
        .order_by(desc(Invoice.created_at))
        .limit(10)
    ))
    csrf_token = get_or_create_csrf_token(request)
    return templates.TemplateResponse(
        request, "app/dashboard.html",
        {"user": user, "invoices": invoices, "csrf_token": csrf_token},
    )


async def _upload_gate(
    request: Request, session: AsyncSession, user: User,
) -> Response | None:
    """Upload gate: email-verified AND positive credit balance.

    Phase 6 originally added a SMS-OTP layer on top, but that path is
    deferred to Phase 7+ (would only be earned in once a free tier
    invites abuse). The phone-verify endpoints and column remain in
    tree as dormant. Until they're rewired, the gate uses email-verify
    (which every signup already clears via Postmark) plus a non-empty
    credit balance.
    """
    if not user.email_verified:
        return RedirectResponse(
            url="/app?reason=email_verify",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    org = await session.scalar(select(Org).where(Org.id == user.org_id))
    assert org is not None
    if org.credit_balance_grosze <= 0:
        return RedirectResponse(
            url="/app/billing?reason=empty",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return None


@router.get("/app/wgraj", response_class=HTMLResponse)
async def upload_page(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    user = await _get_user_or_none(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    gate = await _upload_gate(request, session, user)
    if gate is not None:
        return gate
    csrf_token = get_or_create_csrf_token(request)
    return templates.TemplateResponse(
        request, "app/upload.html",
        {"user": user, "csrf_token": csrf_token},
    )


@router.post("/app/wgraj", response_class=HTMLResponse)
async def upload_submit(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Browser-facing PDF upload. Internally same logic as POST /api/v1/invoices
    but redirects to the detail page on success rather than returning JSON.

    TODO(refactor): factor out shared upload logic into `services/invoice_upload.py`.
    """
    import hashlib
    from src.app.api.invoices import (
        PDF_MAGIC_BYTES, _enqueue_extraction,
    )
    from src.app.storage import get_storage

    user = await _get_user_or_none(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    gate = await _upload_gate(request, session, user)
    if gate is not None:
        return gate

    form = await request.form()
    upload = form.get("pdf")
    csrf_token = get_or_create_csrf_token(request)
    if not hasattr(upload, "read"):
        return templates.TemplateResponse(
            request, "app/upload.html",
            {"user": user, "csrf_token": csrf_token, "error": "Brak pliku."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    content = await upload.read()  # type: ignore[union-attr]
    filename = getattr(upload, "filename", None)

    if len(content) == 0:
        err = "Plik jest pusty."
    elif len(content) > max_bytes:
        err = f"Plik większy niż {settings.max_upload_mb} MB."
    elif not content.startswith(PDF_MAGIC_BYTES):
        err = "To nie jest PDF (brak nagłówka pliku PDF)."
    else:
        err = None

    if err is not None:
        return templates.TemplateResponse(
            request, "app/upload.html",
            {"user": user, "csrf_token": csrf_token, "error": err},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    sha256 = hashlib.sha256(content).hexdigest()
    object_key = f"{user.org_id}/{sha256}.pdf"

    existing = await session.scalar(
        select(Invoice).where(
            Invoice.org_id == user.org_id,
            Invoice.pdf_sha256 == sha256,
            Invoice.deleted_at.is_(None),
        )
    )
    if existing is not None:
        return RedirectResponse(
            url=f"/app/faktury/{existing.id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    storage = get_storage()
    import asyncio as _asyncio
    await _asyncio.to_thread(storage.put, object_key, content)

    invoice = Invoice(
        org_id=user.org_id,
        status="pending",
        pdf_object_key=object_key,
        pdf_size_bytes=len(content),
        pdf_sha256=sha256,
        original_filename=filename,
    )
    session.add(invoice)
    session.add(AuditEvent(
        org_id=user.org_id, user_id=user.id, action="invoice.uploaded",
        payload={"pdf_sha256": sha256, "size_bytes": len(content),
                 "filename": filename, "via": "web"},
        ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
    ))
    await session.commit()
    await session.refresh(invoice)
    await _enqueue_extraction(invoice.id)

    return RedirectResponse(
        url=f"/app/faktury/{invoice.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/app/faktury", response_class=HTMLResponse)
async def invoices_list(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    offset: int = 0,
    limit: int = 50,
) -> Response:
    user = await _get_user_or_none(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    invoices = list(await session.scalars(
        select(Invoice)
        .where(Invoice.org_id == user.org_id, Invoice.deleted_at.is_(None))
        .order_by(desc(Invoice.created_at))
        .limit(limit + 1)
        .offset(offset)
    ))
    has_more = len(invoices) > limit
    invoices = invoices[:limit]

    csrf_token = get_or_create_csrf_token(request)
    return templates.TemplateResponse(
        request, "app/invoices.html",
        {
            "user": user, "csrf_token": csrf_token,
            "invoices": invoices, "offset": offset, "limit": limit,
            "has_more": has_more,
        },
    )


@router.get("/app/faktury/{invoice_id}", response_class=HTMLResponse)
async def invoice_detail(
    request: Request,
    invoice_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Renders the review page when extraction is complete, otherwise
    the status stub with auto-refresh."""
    user = await _get_user_or_none(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    invoice = await session.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.org_id == user.org_id,
            Invoice.deleted_at.is_(None),
        )
    )
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Faktura nie znaleziona")

    csrf_token = get_or_create_csrf_token(request)
    template_name = (
        "app/invoice_review.html"
        if invoice.status == "completed" and invoice.canonical_data
        else "app/invoice_detail_stub.html"
    )
    return templates.TemplateResponse(
        request, template_name,
        {"user": user, "csrf_token": csrf_token, "invoice": invoice},
    )


# Top-level CanonicalInvoice fields that the operator can edit on the
# review page. Everything else (overall_confidence, extraction_warnings,
# source_pdf_id, extracted_at, extracted_model, extraction_version) is
# extraction metadata and stays under server control.
_EDITABLE_FIELDS: frozenset[str] = frozenset({
    "invoice_number", "invoice_type", "issue_date", "sale_date",
    "place_of_issue", "seller", "buyer", "lines", "vat_summary",
    "total_net", "total_vat", "total_gross", "payment", "notes",
})


@router.post("/app/faktury/{invoice_id}/popraw")
async def submit_corrections(
    request: Request,
    invoice_id: UUID,
    csrf_token: Annotated[str, Form()],
    canonical_json: Annotated[str, Form()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Apply operator-typed corrections to an already-extracted invoice.

    The form posts a JSON-serialised partial CanonicalInvoice (only the
    fields the operator can edit). The server merges those on top of the
    existing `canonical_data`, re-runs Pydantic validation + business
    validation, and persists the result. Metadata fields (model, version,
    confidence dicts) are preserved from the prior extraction.

    Response envelope is JSON so the form's fetch() submit can react:
      - 200 {ok: true, redirect: ...}  on success
      - 400 {ok: false, message: ...}  on invalid JSON
      - 422 {ok: false, errors: [...]} on schema validation failure
    """
    import json

    user = await _get_user_or_none(request, session)
    if user is None:
        return JSONResponse(
            {"ok": False, "message": "Sesja wygasła — zaloguj się ponownie."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    _verify_csrf_form(request, csrf_token)

    invoice = await session.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.org_id == user.org_id,
            Invoice.deleted_at.is_(None),
        )
    )
    if invoice is None:
        return JSONResponse(
            {"ok": False, "message": "Faktura nie znaleziona."},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if invoice.canonical_data is None:
        return JSONResponse(
            {"ok": False, "message": "Faktura nie ma jeszcze danych do edycji."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        edits = json.loads(canonical_json)
    except json.JSONDecodeError as e:
        return JSONResponse(
            {"ok": False, "message": f"Niepoprawny JSON: {e}"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not isinstance(edits, dict):
        return JSONResponse(
            {"ok": False, "message": "Oczekiwano obiektu JSON."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Merge edits over the existing canonical_data. Metadata fields are
    # pinned from the original so the operator can't accidentally rewrite
    # extraction telemetry.
    merged: dict[str, Any] = dict(invoice.canonical_data)
    for key, value in edits.items():
        if key in _EDITABLE_FIELDS:
            merged[key] = value

    try:
        validated = CanonicalInvoice.model_validate(merged)
    except ValidationError as e:
        return JSONResponse(
            {"ok": False, "errors": e.errors(include_url=False)},
            status_code=422,
        )

    hard, soft = validate_invoice(validated)
    validated = validated.model_copy(update={
        "extraction_warnings": hard + [f"(soft) {w}" for w in soft],
    })
    canonical_dump = validated.model_dump(mode="json")

    now = datetime.now(timezone.utc)
    if invoice.user_reviewed_at is None:
        invoice.user_reviewed_at = now
    invoice.last_correction_at = now
    invoice.canonical_data = canonical_dump

    session.add(AuditEvent(
        org_id=user.org_id, user_id=user.id, action="invoice.corrected",
        payload={
            "invoice_id": str(invoice.id),
            "hard_warnings_after": len(hard),
            "soft_warnings_after": len(soft),
            "first_review": invoice.user_reviewed_at == now,
        },
        ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
    ))
    await session.commit()

    return JSONResponse({
        "ok": True,
        "redirect": f"/app/faktury/{invoice.id}",
        "hard_warnings": len(hard),
        "soft_warnings": len(soft),
    })


@router.post("/app/faktury/{invoice_id}/ponow-ekstrakcje")
async def reextract_invoice(
    request: Request,
    invoice_id: UUID,
    csrf_token: Annotated[str, Form()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Re-run extraction with Sonnet (skip Haiku).

    Counts against the org's monthly extraction quota (Phase 6 — not yet
    enforced). Re-extraction wipes any prior operator corrections; the
    worker resets `user_reviewed_at` + `last_correction_at` on success.
    """
    from src.app.api.invoices import _enqueue_extraction
    from src.pipeline.extraction.extractor import SONNET_MODEL

    user = await _get_user_or_none(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    _verify_csrf_form(request, csrf_token)

    invoice = await session.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.org_id == user.org_id,
            Invoice.deleted_at.is_(None),
        )
    )
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Faktura nie znaleziona")
    if invoice.status not in ("completed", "failed"):
        # Already pending/processing — don't double-enqueue.
        return RedirectResponse(
            url=f"/app/faktury/{invoice.id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    invoice.status = "pending"
    invoice.extraction_error = None
    session.add(AuditEvent(
        org_id=user.org_id, user_id=user.id, action="invoice.reextract_requested",
        payload={"invoice_id": str(invoice.id), "model": SONNET_MODEL},
        ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
    ))
    await session.commit()
    await _enqueue_extraction(invoice.id, force_model=SONNET_MODEL)

    return RedirectResponse(
        url=f"/app/faktury/{invoice.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


_EXPORT_FORMATS: frozenset[str] = frozenset({"json", "csv", "jpk_fa"})


@router.get("/app/faktury/{invoice_id}/eksport/{fmt}")
async def export_invoice(
    request: Request,
    invoice_id: UUID,
    fmt: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Download a completed invoice as `json` / `csv` / `jpk_fa`.

    Exports require `status=completed` AND non-null `canonical_data`. We
    audit-log every successful export so a paying customer's downloads
    can be reconciled with billing later.

    JPK_FA additionally requires the org to have `nip` + `kod_urzedu`
    set (chunk 4 settings page) — without them, returns 422 with a
    pointer to Settings.
    """
    from src.models.invoice import CanonicalInvoice
    from src.pipeline.export import csv_export, json_export, jpk_fa as jpk_fa_export

    user = await _get_user_or_none(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    if fmt not in _EXPORT_FORMATS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Nieobsługiwany format: {fmt}",
        )

    invoice = await session.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.org_id == user.org_id,
            Invoice.deleted_at.is_(None),
        )
    )
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Faktura nie znaleziona")
    if invoice.status != "completed" or not invoice.canonical_data:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Faktura nie jest jeszcze gotowa do eksportu.",
        )

    try:
        canonical = CanonicalInvoice.model_validate(invoice.canonical_data)
    except ValidationError as e:
        logger.error("export: stored canonical_data fails validation for %s: %s",
                     invoice.id, e)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Dane faktury są uszkodzone — skontaktuj się z pomocą.",
        ) from e

    if fmt == "json":
        content = json_export.to_bytes(canonical)
        media_type = "application/json; charset=utf-8"
        suffix = "json"
    elif fmt == "csv":
        content = csv_export.to_bytes(canonical)
        media_type = "text/csv; charset=utf-8"
        suffix = "csv"
    else:  # jpk_fa
        org = await session.scalar(select(Org).where(Org.id == user.org_id))
        assert org is not None  # user.org_id FK guarantees this
        try:
            content = jpk_fa_export.to_bytes(
                canonical,
                org_name=org.name,
                org_nip=org.nip,
                org_kod_urzedu=org.kod_urzedu,
            )
        except jpk_fa_export.JpkFaExportError as e:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, str(e),
            ) from e
        media_type = "application/xml; charset=utf-8"
        suffix = "xml"

    safe_number = "".join(
        c if c.isalnum() or c in "-_." else "_"
        for c in canonical.invoice_number
    ) or str(invoice.id)
    filename = f"{safe_number}.{suffix}"

    session.add(AuditEvent(
        org_id=user.org_id, user_id=user.id, action="invoice.exported",
        payload={
            "invoice_id": str(invoice.id),
            "format": fmt,
            "size_bytes": len(content),
        },
        ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
    ))
    await session.commit()

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/app/faktury/{invoice_id}/pdf")
async def invoice_pdf_proxy(
    request: Request,
    invoice_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Stream the source PDF from object storage.

    Used by the `<embed>` tag in the review page. Server-side proxy keeps
    object-storage credentials private and avoids CORS / presigned-URL
    edge cases. For high-traffic prod, switch to presigned URLs.
    """
    import asyncio as _asyncio
    from src.app.storage import get_storage

    user = await _get_user_or_none(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    invoice = await session.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.org_id == user.org_id,
            Invoice.deleted_at.is_(None),
        )
    )
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Faktura nie znaleziona")

    storage = get_storage()
    content = await _asyncio.to_thread(storage.get, invoice.pdf_object_key)
    filename = invoice.original_filename or f"invoice-{invoice.id}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ── Org settings (Phase 4 chunk 4) ────────────────────────────────────────


def _validate_settings_form(
    *,
    name: str,
    nip: str,
    regon: str,
    kod_urzedu: str,
) -> tuple[dict[str, str | None], list[str]]:
    """Normalise and validate the settings form. Empty NIP/REGON/KodUrzedu
    are allowed at save time — they're only required at JPK_FA export.
    Bad-but-non-empty values are rejected so the operator finds out now
    instead of at export time."""
    cleaned: dict[str, str | None] = {}
    errors: list[str] = []

    cleaned_name = name.strip()
    if not cleaned_name:
        errors.append("Nazwa organizacji jest wymagana.")
    elif len(cleaned_name) > 255:
        errors.append("Nazwa organizacji może mieć maksymalnie 255 znaków.")
    cleaned["name"] = cleaned_name

    nip_norm = normalize_nip(nip)
    if not nip_norm:
        cleaned["nip"] = None
    elif is_valid_nip(nip_norm):
        cleaned["nip"] = nip_norm
    else:
        errors.append("NIP jest niepoprawny (10 cyfr, suma kontrolna mod-11).")
        cleaned["nip"] = nip_norm

    regon_norm = normalize_regon(regon)
    if not regon_norm:
        cleaned["regon"] = None
    elif is_valid_regon(regon_norm):
        cleaned["regon"] = regon_norm
    else:
        errors.append("REGON jest niepoprawny (9 lub 14 cyfr, suma kontrolna).")
        cleaned["regon"] = regon_norm

    kod_norm = normalize_kod_urzedu(kod_urzedu)
    if not kod_norm:
        cleaned["kod_urzedu"] = None
    elif is_valid_kod_urzedu(kod_norm):
        cleaned["kod_urzedu"] = kod_norm
    else:
        errors.append("Kod urzędu skarbowego musi mieć dokładnie 4 cyfry (np. 0202).")
        cleaned["kod_urzedu"] = kod_norm

    return cleaned, errors


@router.get("/app/ustawienia", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    user = await _get_user_or_none(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    org = await session.scalar(select(Org).where(Org.id == user.org_id))
    assert org is not None  # FK guarantees existence
    csrf_token = get_or_create_csrf_token(request)
    return templates.TemplateResponse(
        request, "app/settings.html",
        {
            "user": user, "csrf_token": csrf_token,
            "form": {
                "name": org.name, "nip": org.nip,
                "regon": org.regon, "kod_urzedu": org.kod_urzedu,
            },
            "errors": [], "saved": False,
        },
    )


@router.post("/app/ustawienia", response_class=HTMLResponse)
async def settings_save(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    csrf_token: Annotated[str, Form()],
    name: Annotated[str, Form()],
    nip: Annotated[str, Form()] = "",
    regon: Annotated[str, Form()] = "",
    kod_urzedu: Annotated[str, Form()] = "",
) -> Response:
    user = await _get_user_or_none(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    _verify_csrf_form(request, csrf_token)

    org = await session.scalar(select(Org).where(Org.id == user.org_id))
    assert org is not None

    cleaned, errors = _validate_settings_form(
        name=name, nip=nip, regon=regon, kod_urzedu=kod_urzedu,
    )

    new_csrf = get_or_create_csrf_token(request)
    if errors:
        return templates.TemplateResponse(
            request, "app/settings.html",
            {
                "user": user, "csrf_token": new_csrf,
                "form": cleaned, "errors": errors, "saved": False,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    changed: dict[str, tuple[str | None, str | None]] = {}
    for field in ("name", "nip", "regon", "kod_urzedu"):
        before = getattr(org, field)
        after = cleaned[field]
        if before != after:
            changed[field] = (before, after)
            setattr(org, field, after)

    if changed:
        session.add(AuditEvent(
            org_id=org.id, user_id=user.id, action="org.settings_updated",
            payload={"changed_fields": sorted(changed.keys())},
            ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
        ))
        await session.commit()

    return templates.TemplateResponse(
        request, "app/settings.html",
        {
            "user": user, "csrf_token": new_csrf,
            "form": {
                "name": org.name, "nip": org.nip,
                "regon": org.regon, "kod_urzedu": org.kod_urzedu,
            },
            "errors": [], "saved": True,
        },
    )


# ── Billing top-up (Phase 6) ──────────────────────────────────────────────

# Operator-allowed top-up amounts (PLN grosze). Hard-coded server-side
# so a tampered POST can't request arbitrary amounts. 20/50/100 PLN per
# HANDOFF "Phase 6 endpoint wiring §2".
TOPUP_AMOUNTS_GROSZE: frozenset[int] = frozenset({2000, 5000, 10000})


@router.get("/app/billing", response_class=HTMLResponse)
async def billing_page(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    user = await _get_user_or_none(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    org = await session.scalar(select(Org).where(Org.id == user.org_id))
    assert org is not None
    csrf_token = get_or_create_csrf_token(request)
    return templates.TemplateResponse(
        request, "app/billing.html",
        {
            "user": user, "csrf_token": csrf_token,
            "balance_grosze": org.credit_balance_grosze,
            "amounts_grosze": sorted(TOPUP_AMOUNTS_GROSZE),
            "invoice_price_grosze": get_settings().invoice_price_grosze,
            "result": request.query_params.get("result"),
            "reason": request.query_params.get("reason"),
        },
    )


@router.post("/app/billing/topup")
async def billing_topup(
    request: Request,
    csrf_token: Annotated[str, Form()],
    amount_grosze: Annotated[int, Form()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Create a Stripe Checkout Session for a top-up of `amount_grosze`
    and 303 the user to it. ConsoleStripeClient returns a fake localhost
    URL in dev; RealStripeClient returns `checkout.stripe.com/...`."""
    import uuid as _uuid

    user = await _get_user_or_none(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    _verify_csrf_form(request, csrf_token)

    if amount_grosze not in TOPUP_AMOUNTS_GROSZE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Nieobsługiwana kwota doładowania ({amount_grosze} gr).",
        )

    org = await session.scalar(select(Org).where(Org.id == user.org_id))
    assert org is not None

    stripe = get_stripe_client()
    try:
        if not org.stripe_customer_id:
            org.stripe_customer_id = await stripe.ensure_customer(
                email=user.email, org_id=str(org.id),
            )
            await session.flush()

        idem_key = _uuid.uuid4().hex
        settings = get_settings()
        success_url = f"{settings.app_base_url}/app/billing?result=ok"
        cancel_url = f"{settings.app_base_url}/app/billing?result=cancel"
        checkout_url = await stripe.create_topup_session(
            customer_id=org.stripe_customer_id,
            amount_grosze=amount_grosze,
            success_url=success_url,
            cancel_url=cancel_url,
            idem_key=idem_key,
        )
    except StripeError as e:
        logger.warning("topup failed for org %s: %s", org.id, e)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Doładowanie chwilowo niedostępne — spróbuj za moment.",
        ) from e

    session.add(AuditEvent(
        org_id=org.id, user_id=user.id, action="billing.topup_started",
        payload={"amount_grosze": amount_grosze, "idem_key": idem_key},
        ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
    ))
    await session.commit()

    return RedirectResponse(url=checkout_url, status_code=status.HTTP_303_SEE_OTHER)

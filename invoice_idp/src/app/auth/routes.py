"""Auth endpoints — signup, email verification, login, logout, password reset.

Phase 2 surface is JSON-API; HTMX views land in Phase 4 and will wrap
these same operations. The verification + reset email links point at
JSON endpoints for now — clicking them in a browser shows the raw JSON
response, which is acceptable for dev.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.auth.csrf import get_or_create_csrf_token, verify_csrf
from src.app.auth.deps import get_current_user
from src.app.auth.passwords import hash_password, verify_password
from src.app.config import get_settings
from src.app.db import get_session
from src.app.email import get_emailer
from src.app.sms import VerifyError, get_sms_verifier
from src.models.audit import AuditEvent
from src.models.org import Org
from src.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])

VERIFICATION_TOKEN_TTL_HOURS = 24
PASSWORD_RESET_TTL_MINUTES = 15
PHONE_RESEND_COOLDOWN_SECONDS = 60


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)


class PhoneStartRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=20)


class PhoneCheckRequest(BaseModel):
    code: str = Field(min_length=4, max_length=10)


class UserResponse(BaseModel):
    user_id: UUID
    email: str
    email_verified: bool
    org_id: UUID


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _send_verification_email(user: User) -> None:
    settings = get_settings()
    link = f"{settings.app_base_url}/auth/verify-email?token={user.email_verification_token}"
    try:
        await get_emailer().send(
            to=user.email,
            subject="Potwierdź adres email — Faktomat",
            text=(
                "Witaj!\n\n"
                f"Kliknij link, aby potwierdzić swój adres email:\n{link}\n\n"
                f"Link ważny {VERIFICATION_TOKEN_TTL_HOURS}h."
            ),
        )
    except Exception as e:  # noqa: BLE001 — never let email failures break signup
        logger.warning("verification email failed for %s: %s", user.email, e)


async def _send_password_reset_email(user: User) -> None:
    settings = get_settings()
    link = f"{settings.app_base_url}/auth/reset-password?token={user.password_reset_token}"
    try:
        await get_emailer().send(
            to=user.email,
            subject="Reset hasła — Faktomat",
            text=(
                "Kliknij link, aby zresetować hasło "
                f"(ważny {PASSWORD_RESET_TTL_MINUTES} minut):\n{link}\n\n"
                "Jeśli nie prosiłeś o reset, zignoruj tę wiadomość."
            ),
        )
    except Exception as e:  # noqa: BLE001 — same rationale as above
        logger.warning("password reset email failed for %s: %s", user.email, e)


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """Create a new Org + User, send a verification email."""
    existing = await session.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already in use")

    org = Org(name=payload.email)
    session.add(org)
    await session.flush()

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        org_id=org.id,
        email_verification_token=secrets.token_urlsafe(48),
        email_verification_sent_at=datetime.now(timezone.utc),
    )
    session.add(user)
    session.add(AuditEvent(
        org_id=org.id,
        user_id=user.id,
        action="auth.signup",
        payload={"email": user.email},
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    ))
    await session.commit()
    await session.refresh(user)

    await _send_verification_email(user)

    return {
        "user_id": str(user.id),
        "email": user.email,
        "message": "Verification email sent",
    }


@router.get("/verify-email")
async def verify_email(
    token: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    user = await session.scalar(
        select(User).where(User.email_verification_token == token)
    )
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid token")

    if user.email_verification_sent_at is not None:
        expiry = user.email_verification_sent_at + timedelta(
            hours=VERIFICATION_TOKEN_TTL_HOURS
        )
        if expiry < datetime.now(timezone.utc):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Token expired")

    user.email_verified = True
    user.email_verification_token = None
    user.email_verification_sent_at = None

    session.add(AuditEvent(
        org_id=user.org_id,
        user_id=user.id,
        action="auth.email_verified",
    ))
    await session.commit()

    return {"message": "Email verified"}


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserResponse:
    user = await session.scalar(
        select(User).where(User.email == payload.email, User.deleted_at.is_(None))
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        session.add(AuditEvent(
            user_id=user.id if user else None,
            action="auth.login_failed",
            payload={
                "email": payload.email,
                "reason": "no_user" if user is None else "wrong_password",
            },
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ))
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    user.last_login_at = datetime.now(timezone.utc)
    request.session["user_id"] = str(user.id)
    # Rotate CSRF token on successful login to prevent fixation
    get_or_create_csrf_token(request)

    session.add(AuditEvent(
        org_id=user.org_id,
        user_id=user.id,
        action="auth.login",
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    ))
    await session.commit()

    return UserResponse(
        user_id=user.id,
        email=user.email,
        email_verified=user.email_verified,
        org_id=user.org_id,
    )


@router.post("/logout", dependencies=[Depends(verify_csrf)])
async def logout(request: Request) -> dict[str, str]:
    request.session.clear()
    return {"message": "Logged out"}


@router.post("/forgot-password")
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """Send a password-reset link if an account exists. Response is
    identical regardless — never disclose whether the email is registered."""
    user = await session.scalar(
        select(User).where(User.email == payload.email, User.deleted_at.is_(None))
    )
    if user is not None:
        user.password_reset_token = secrets.token_urlsafe(48)
        user.password_reset_expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_TTL_MINUTES)
        )
        session.add(AuditEvent(
            org_id=user.org_id,
            user_id=user.id,
            action="auth.password_reset_requested",
            ip=_client_ip(request),
        ))
        await session.commit()
        await _send_password_reset_email(user)

    return {"message": "If an account with that email exists, a reset link was sent"}


@router.post("/reset-password")
async def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    user = await session.scalar(
        select(User).where(
            User.password_reset_token == payload.token,
            User.deleted_at.is_(None),
        )
    )
    if user is None or user.password_reset_expires_at is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid token")
    if user.password_reset_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Token expired")

    user.password_hash = hash_password(payload.password)
    user.password_reset_token = None
    user.password_reset_expires_at = None

    session.add(AuditEvent(
        org_id=user.org_id,
        user_id=user.id,
        action="auth.password_reset",
        ip=_client_ip(request),
    ))
    await session.commit()

    return {"message": "Password reset"}


@router.get("/me")
async def me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserResponse:
    return UserResponse(
        user_id=current_user.id,
        email=current_user.email,
        email_verified=current_user.email_verified,
        org_id=current_user.org_id,
    )


@router.get("/csrf")
async def csrf_endpoint(request: Request) -> dict[str, str]:
    """Issue (or fetch) the current session's CSRF token.

    Frontends must include the returned token in the `X-CSRF-Token`
    header on every state-changing request to authenticated endpoints.
    """
    return {"csrf_token": get_or_create_csrf_token(request)}


# ── Phase 6 phone verification ────────────────────────────────────────


def _normalize_e164(phone: str) -> str | None:
    """Strip spaces/dashes; require leading '+' and 8-15 digits after.

    Returns the canonical `+CCNNNNNNNNN` form or None if invalid. E.164
    allows 15 digits max (country code included); 8 is a conservative
    minimum so single-country mistypes are rejected.
    """
    cleaned = "".join(c for c in phone if c.isdigit() or c == "+")
    if not cleaned.startswith("+"):
        return None
    digits = cleaned[1:]
    if not digits.isdigit() or not (8 <= len(digits) <= 15):
        return None
    return f"+{digits}"


@router.post("/phone/start", dependencies=[Depends(verify_csrf)])
async def phone_start(
    payload: PhoneStartRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """Send a one-time verification code to the supplied phone number.

    Stamps `User.phone_number` + `phone_verification_sent_at`. Re-sends
    are rate-limited at `PHONE_RESEND_COOLDOWN_SECONDS`. Phone goes
    through `get_sms_verifier()` — Twilio Verify in prod, console code
    (last 6 digits of phone) in dev.
    """
    if current_user.phone_verified_at is not None:
        # Re-verification requires explicit reset; not in scope for V1.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Numer telefonu jest już zweryfikowany.",
        )

    e164 = _normalize_e164(payload.phone)
    if e164 is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Numer telefonu musi być w formacie E.164 (np. +48600123456).",
        )

    now = datetime.now(timezone.utc)
    last_sent = current_user.phone_verification_sent_at
    if last_sent is not None:
        if last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=timezone.utc)
        elapsed = (now - last_sent).total_seconds()
        if elapsed < PHONE_RESEND_COOLDOWN_SECONDS:
            wait = int(PHONE_RESEND_COOLDOWN_SECONDS - elapsed)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Poczekaj {wait}s przed ponownym wysłaniem kodu.",
            )

    try:
        await get_sms_verifier().start_verification(e164)
    except VerifyError as e:
        logger.warning("phone start_verification failed for %s: %s",
                       current_user.id, e)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Nie udało się wysłać SMS-a — spróbuj za chwilę.",
        ) from e

    current_user.phone_number = e164
    current_user.phone_verification_sent_at = now
    session.add(AuditEvent(
        org_id=current_user.org_id, user_id=current_user.id,
        action="auth.phone_start", ip=_client_ip(request),
    ))
    await session.commit()
    return {"message": "SMS sent"}


@router.post("/phone/check", dependencies=[Depends(verify_csrf)])
async def phone_check(
    payload: PhoneCheckRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """Confirm the operator-supplied OTP and stamp `phone_verified_at`."""
    if current_user.phone_verified_at is not None:
        return {"message": "Phone already verified"}
    if current_user.phone_number is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Brak rozpoczętej weryfikacji telefonu — wyślij najpierw kod.",
        )

    try:
        ok = await get_sms_verifier().check_code(
            current_user.phone_number, payload.code,
        )
    except VerifyError as e:
        logger.warning("phone check_code failed for %s: %s", current_user.id, e)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Weryfikacja chwilowo niedostępna — spróbuj ponownie.",
        ) from e

    if not ok:
        session.add(AuditEvent(
            org_id=current_user.org_id, user_id=current_user.id,
            action="auth.phone_check_failed", ip=_client_ip(request),
        ))
        await session.commit()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Kod nieprawidłowy lub wygasł.",
        )

    current_user.phone_verified_at = datetime.now(timezone.utc)
    session.add(AuditEvent(
        org_id=current_user.org_id, user_id=current_user.id,
        action="auth.phone_verified", ip=_client_ip(request),
    ))
    await session.commit()
    return {"message": "Phone verified"}

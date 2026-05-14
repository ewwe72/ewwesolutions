"""User account — login identity inside an Org."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.db import Base

if TYPE_CHECKING:
    from src.models.org import Org


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))

    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_verification_token: Mapped[str | None] = mapped_column(String(64), index=True)
    email_verification_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    password_reset_token: Mapped[str | None] = mapped_column(String(64), index=True)
    password_reset_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Phase 6 — phone verification (Twilio Verify). E.164 number, then
    # Twilio holds the OTP server-side; we only stamp when verified.
    phone_number: Mapped[str | None] = mapped_column(String(20))
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    phone_verification_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    org_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    org: Mapped["Org"] = relationship(back_populates="users")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True,
    )

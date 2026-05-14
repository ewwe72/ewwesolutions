"""Organisation — billing entity and tenant boundary.

Per spec §3: "Single-tenant accounts (one user = one organisation;
multi-user later)". The schema supports 1:N today so multi-user is
a code change, not a migration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.db import Base

if TYPE_CHECKING:
    from src.models.user import User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255))
    nip: Mapped[str | None] = mapped_column(String(10), index=True)
    regon: Mapped[str | None] = mapped_column(String(14))
    kod_urzedu: Mapped[str | None] = mapped_column(String(10))  # JPK_FA tax office code
    plan: Mapped[str] = mapped_column(String(32), default="free")  # free/starter/pro/business/biuro
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), index=True, unique=True)
    # Phase 6 — prepaid credit balance in PLN groszy. Each successful
    # extraction debits `Settings.invoice_price_grosze`; top-ups via
    # Stripe Checkout credit it. Locks uploads when ≤ 0.
    credit_balance_grosze: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True,
    )

    users: Mapped[list["User"]] = relationship(
        back_populates="org",
        cascade="all, delete-orphan",
    )

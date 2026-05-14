"""Per-org per-month invoice counter — drives plan enforcement and overage billing.

One row per (org_id, year_month). Incremented on every successful
extraction; compared to plan limit on each upload; overage tracked
separately for end-of-month invoicing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Usage(Base):
    __tablename__ = "usage"
    __table_args__ = (
        UniqueConstraint("org_id", "year_month", name="uq_usage_org_month"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    year_month: Mapped[str] = mapped_column(String(7), index=True)  # "2026-05"
    invoices_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    overage_invoices: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False,
    )

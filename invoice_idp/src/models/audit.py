"""Append-only audit log.

Per spec §12: every extraction, every export, every user-facing action
with timestamp + user_id + IP + action. Retention 24 months.
Immutable (no UPDATE / DELETE in normal app paths).

Actions are stable strings like `auth.signup`, `invoice.extracted`,
`export.delivered`. Payload is free-form JSON for action-specific
context (e.g. failed-login reason, export format, plan-change details).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    org_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("orgs.id"), index=True)
    user_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True,
    )

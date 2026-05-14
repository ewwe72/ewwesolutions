"""SQLAlchemy `Invoice` row — DB representation of one uploaded PDF.

This is the *persistence* model, distinct from the Pydantic
`CanonicalInvoice` in `invoice.py` (which is the data contract / DTO).

Flow:
  - Upload endpoint creates an Invoice with status='pending', stores
    the PDF in S3, enqueues an extraction job.
  - Worker pulls the job, loads the PDF, runs extract_from_pdf, writes
    the resulting CanonicalInvoice JSON into `canonical_data`, flips
    status to 'completed' (or 'failed' with `extraction_error`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.app.db import Base

InvoiceStatus = Literal["pending", "processing", "completed", "failed"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("orgs.id", ondelete="CASCADE"), index=True,
    )

    status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False, index=True,
    )

    # Object storage pointer + dedup metadata
    pdf_object_key: Mapped[str] = mapped_column(String(255), unique=True)
    pdf_size_bytes: Mapped[int] = mapped_column(Integer)
    pdf_sha256: Mapped[str] = mapped_column(String(64), index=True)
    original_filename: Mapped[str | None] = mapped_column(String(255))

    # Canonical-form invoice data once extraction completes. JSONB rather
    # than a typed column tree because the schema can evolve via prompt
    # versioning without DB migrations.
    canonical_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Extraction telemetry
    extraction_path: Mapped[str | None] = mapped_column(String(32))  # haiku-only / forced-{model}
    extraction_model: Mapped[str | None] = mapped_column(String(64))
    extraction_version: Mapped[str | None] = mapped_column(String(16))
    overall_confidence: Mapped[float | None] = mapped_column()
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_error: Mapped[str | None] = mapped_column(String(1024))

    # Operator-correction telemetry (Phase 4 chunk 3b). user_reviewed_at
    # stamps the first save on the review page (= "operator has signed off
    # on this invoice"); last_correction_at updates on every save.
    user_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_correction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True,
    )

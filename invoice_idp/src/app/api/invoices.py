"""Invoice upload + retrieval endpoints (V1.0 surface).

POST /api/v1/invoices  — multipart PDF upload, returns job id (= invoice row id)
GET  /api/v1/invoices  — list current org's invoices (paginated)
GET  /api/v1/invoices/{id} — fetch a single invoice's canonical data + status

The upload flow:
  1. Validate MIME + size + that file is a parseable PDF
  2. Hash the bytes, write to S3 under `<org_id>/<sha256>.pdf`
  3. Create Invoice row with status='pending'
  4. Enqueue extraction job to arq
  5. Return invoice id; client polls GET /api/v1/invoices/{id}

Idempotency: if (org_id, sha256) already exists, return the existing
invoice id instead of creating a duplicate — same PDF re-uploaded is
treated as the same job.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Annotated, Any
from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.auth.deps import require_verified_email
from src.app.config import get_settings
from src.app.db import get_session
from src.app.storage import get_storage
from src.models.audit import AuditEvent
from src.models.invoice_record import Invoice
from src.models.user import User

router = APIRouter(prefix="/api/v1", tags=["invoices"])

PDF_MAGIC_BYTES = b"%PDF-"


class InvoiceSummary(BaseModel):
    id: UUID
    status: str
    original_filename: str | None
    pdf_sha256: str
    overall_confidence: float | None
    extracted_at: str | None
    extraction_error: str | None


class InvoiceDetail(InvoiceSummary):
    canonical_data: dict[str, Any] | None


def _redis_settings() -> RedisSettings:
    """Parse REDIS_URL into arq's RedisSettings struct."""
    settings = get_settings()
    return RedisSettings.from_dsn(settings.redis_url)


async def _enqueue_extraction(
    invoice_id: UUID, force_model: str | None = None
) -> None:
    redis = await create_pool(_redis_settings())
    try:
        if force_model is None:
            await redis.enqueue_job("extract_invoice_task", str(invoice_id))
        else:
            await redis.enqueue_job(
                "extract_invoice_task", str(invoice_id), force_model
            )
    finally:
        await redis.aclose()


@router.post(
    "/invoices",
    status_code=status.HTTP_201_CREATED,
    response_model=InvoiceSummary,
)
async def upload_invoice(
    request: Request,
    pdf: Annotated[UploadFile, File(description="PDF invoice")],
    current_user: Annotated[User, Depends(require_verified_email)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InvoiceSummary:
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024

    content = await pdf.read()
    if len(content) == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    if len(content) > max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds {settings.max_upload_mb} MB cap",
        )
    if not content.startswith(PDF_MAGIC_BYTES):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not a PDF (magic bytes missing)")

    sha256 = hashlib.sha256(content).hexdigest()
    org_id = current_user.org_id
    object_key = f"{org_id}/{sha256}.pdf"

    existing = await session.scalar(
        select(Invoice).where(
            Invoice.org_id == org_id,
            Invoice.pdf_sha256 == sha256,
            Invoice.deleted_at.is_(None),
        )
    )
    if existing is not None:
        return InvoiceSummary(
            id=existing.id,
            status=existing.status,
            original_filename=existing.original_filename,
            pdf_sha256=existing.pdf_sha256,
            overall_confidence=existing.overall_confidence,
            extracted_at=existing.extracted_at.isoformat() if existing.extracted_at else None,
            extraction_error=existing.extraction_error,
        )

    storage = get_storage()
    await asyncio.to_thread(storage.put, object_key, content)

    invoice = Invoice(
        org_id=org_id,
        status="pending",
        pdf_object_key=object_key,
        pdf_size_bytes=len(content),
        pdf_sha256=sha256,
        original_filename=pdf.filename,
    )
    session.add(invoice)

    session.add(AuditEvent(
        org_id=org_id,
        user_id=current_user.id,
        action="invoice.uploaded",
        payload={
            "pdf_sha256": sha256,
            "size_bytes": len(content),
            "filename": pdf.filename,
        },
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    ))
    await session.commit()
    await session.refresh(invoice)

    await _enqueue_extraction(invoice.id)

    return InvoiceSummary(
        id=invoice.id,
        status=invoice.status,
        original_filename=invoice.original_filename,
        pdf_sha256=invoice.pdf_sha256,
        overall_confidence=None,
        extracted_at=None,
        extraction_error=None,
    )


@router.get("/invoices", response_model=list[InvoiceSummary])
async def list_invoices(
    current_user: Annotated[User, Depends(require_verified_email)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[InvoiceSummary]:
    rows = await session.scalars(
        select(Invoice)
        .where(Invoice.org_id == current_user.org_id, Invoice.deleted_at.is_(None))
        .order_by(desc(Invoice.created_at))
        .limit(limit)
        .offset(offset)
    )
    return [
        InvoiceSummary(
            id=r.id,
            status=r.status,
            original_filename=r.original_filename,
            pdf_sha256=r.pdf_sha256,
            overall_confidence=r.overall_confidence,
            extracted_at=r.extracted_at.isoformat() if r.extracted_at else None,
            extraction_error=r.extraction_error,
        )
        for r in rows
    ]


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetail)
async def get_invoice(
    invoice_id: UUID,
    current_user: Annotated[User, Depends(require_verified_email)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InvoiceDetail:
    invoice = await session.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.org_id == current_user.org_id,
            Invoice.deleted_at.is_(None),
        )
    )
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")

    return InvoiceDetail(
        id=invoice.id,
        status=invoice.status,
        original_filename=invoice.original_filename,
        pdf_sha256=invoice.pdf_sha256,
        overall_confidence=invoice.overall_confidence,
        extracted_at=invoice.extracted_at.isoformat() if invoice.extracted_at else None,
        extraction_error=invoice.extraction_error,
        canonical_data=invoice.canonical_data,
    )

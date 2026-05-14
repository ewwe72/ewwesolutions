"""arq background worker — picks up pending invoices and runs extraction.

Run:
    python -m arq src.app.workers.extract.WorkerSettings
    # or with uvicorn-style auto-reload during dev:
    python -m arq src.app.workers.extract.WorkerSettings --watch src/

Each job:
  1. Loads Invoice row by id
  2. Marks status='processing'
  3. Fetches the PDF bytes from object storage
  4. Calls `extract_from_pdf` via the configured provider (Bedrock in prod)
  5. Persists CanonicalInvoice JSON + telemetry into the row
  6. Marks status='completed' (or 'failed' with the error message)
  7. Writes an audit event
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from arq.connections import RedisSettings
from sqlalchemy import select

from src.app.config import get_settings
from src.app.db import SessionLocal
from src.app.storage import get_storage
from src.models.audit import AuditEvent
from src.models.invoice_record import Invoice
from src.models.org import Org
from src.pipeline.extraction.extractor import (
    extract_from_pdf,
    extract_from_pdf_force,
    get_extractor,
)

logger = logging.getLogger(__name__)


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


def _serialise_canonical(canonical: Any) -> dict[str, Any]:
    """Pydantic model → JSON-safe dict (handles Decimal, datetime, Enum)."""
    dumped = canonical.model_dump(mode="json")
    assert isinstance(dumped, dict)
    return dumped


async def extract_invoice_task(
    ctx: dict[str, Any],
    invoice_id: str,
    force_model: str | None = None,
) -> dict[str, Any]:
    """Worker entry point — process one Invoice row through the pipeline.

    `force_model` bypasses Haiku-first routing and runs that specific
    model directly. Used by the re-extract button on the review page.
    """
    iid = UUID(invoice_id)
    storage = get_storage()
    extractor = get_extractor()

    async with SessionLocal() as session:
        invoice = await session.scalar(select(Invoice).where(Invoice.id == iid))
        if invoice is None:
            logger.warning("invoice %s vanished before extraction", iid)
            return {"status": "missing"}

        invoice.status = "processing"
        await session.commit()

    # PDF fetch + extraction happen outside the DB transaction so we
    # don't hold a connection during a 5-30s LLM call.
    try:
        pdf_bytes = await asyncio.to_thread(storage.get, invoice.pdf_object_key)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = Path(tmp.name)
        try:
            if force_model is None:
                run = await asyncio.to_thread(extract_from_pdf, tmp_path, extractor)
            else:
                run = await asyncio.to_thread(
                    extract_from_pdf_force, tmp_path, extractor, force_model
                )
        finally:
            tmp_path.unlink(missing_ok=True)

        canonical_data = _serialise_canonical(run.invoice)

        async with SessionLocal() as session:
            invoice = await session.scalar(select(Invoice).where(Invoice.id == iid))
            if invoice is None:
                return {"status": "missing"}
            invoice.status = "completed"
            invoice.canonical_data = canonical_data
            invoice.extraction_path = run.path_taken
            invoice.extraction_model = run.invoice.extracted_model
            invoice.extraction_version = run.invoice.extraction_version
            invoice.overall_confidence = run.invoice.overall_confidence
            invoice.extracted_at = datetime.now(timezone.utc)
            invoice.extraction_error = None
            # A fresh extraction wipes any prior operator review — the
            # data the operator signed off on is no longer what's stored.
            invoice.user_reviewed_at = None
            invoice.last_correction_at = None

            session.add(AuditEvent(
                org_id=invoice.org_id,
                action="invoice.extracted",
                payload={
                    "invoice_id": str(invoice.id),
                    "path": run.path_taken,
                    "confidence": run.invoice.overall_confidence,
                    "hard_warnings": sum(
                        1 for w in run.invoice.extraction_warnings if not w.startswith("(soft)")
                    ),
                },
            ))

            # Debit the per-invoice price from the org's credit balance.
            # Per HANDOFF "Phase 6 endpoint wiring §worker": don't block
            # extraction even if this goes negative on the last invoice —
            # the upload gate has already done its job; let the in-flight
            # job complete. The audit event below is the source of truth
            # for reconciliation.
            price_grosze = get_settings().invoice_price_grosze
            org = await session.scalar(select(Org).where(Org.id == invoice.org_id))
            if org is not None:
                org.credit_balance_grosze = org.credit_balance_grosze - price_grosze
                session.add(AuditEvent(
                    org_id=invoice.org_id,
                    action="billing.extraction_debited",
                    payload={
                        "invoice_id": str(invoice.id),
                        "amount_grosze": price_grosze,
                        "balance_after_grosze": org.credit_balance_grosze,
                    },
                ))
            await session.commit()

        return {"status": "completed", "confidence": run.invoice.overall_confidence}

    except Exception as e:  # noqa: BLE001
        logger.exception("extraction failed for invoice %s", iid)
        async with SessionLocal() as session:
            invoice = await session.scalar(select(Invoice).where(Invoice.id == iid))
            if invoice is not None:
                invoice.status = "failed"
                invoice.extraction_error = f"{type(e).__name__}: {e}"[:1024]
                session.add(AuditEvent(
                    org_id=invoice.org_id,
                    action="invoice.extraction_failed",
                    payload={"invoice_id": str(invoice.id), "error": invoice.extraction_error},
                ))
                await session.commit()
        return {"status": "failed", "error": str(e)[:200]}


class WorkerSettings:
    """arq picks this up via `python -m arq src.app.workers.extract.WorkerSettings`."""

    functions = [extract_invoice_task]
    redis_settings = _redis_settings()
    max_jobs = 4         # extraction is I/O + LLM bound, modest parallelism
    job_timeout = 300    # 5 min per invoice (covers slow Bedrock responses)
    keep_result = 3600   # 1 hour

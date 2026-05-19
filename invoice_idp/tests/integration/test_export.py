"""Integration tests for the export endpoint (Phase 4 chunk 3c).

GET /app/faktury/{id}/eksport/{json,csv} — auth required, org-scoped,
gated on status=completed + canonical_data present, audit-logged.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.audit import AuditEvent
from src.models.invoice import (
    CanonicalInvoice,
    Counterparty,
    Currency,
    InvoiceType,
    LineItem,
    Money,
    PaymentInfo,
    VATRate,
    VATSummaryEntry,
)
from src.models.invoice_record import Invoice
from src.models.user import User


def _pln(value: str) -> Money:
    return Money(amount=Decimal(value), currency=Currency.PLN)


def _sample_canonical() -> dict[str, Any]:
    inv = CanonicalInvoice(
        invoice_number="FV/01/2026/001",
        invoice_type=InvoiceType.REGULAR,
        issue_date=date(2026, 5, 1),
        seller=Counterparty(name="Sprzedawca sp. z o.o.", nip="1234567819"),
        buyer=Counterparty(name="Nabywca sp. z o.o.", nip="1234567819"),
        lines=[LineItem(
            line_no=1,
            description="Konsultacja",
            quantity=Decimal("1"),
            unit="godz.",
            unit_price_net=_pln("100.00"),
            vat_rate=VATRate.R23,
            net_value=_pln("100.00"),
            vat_value=_pln("23.00"),
            gross_value=_pln("123.00"),
        )],
        vat_summary=[VATSummaryEntry(
            rate=VATRate.R23,
            net_total=_pln("100.00"),
            vat_total=_pln("23.00"),
            gross_total=_pln("123.00"),
        )],
        total_net=_pln("100.00"),
        total_vat=_pln("23.00"),
        total_gross=_pln("123.00"),
        payment=PaymentInfo(due_date=date(2026, 5, 15)),
        overall_confidence=0.93,
        source_pdf_id="test.pdf",
        extracted_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
        extracted_model="claude-haiku-4-5",
        extraction_version="v1.1",
    )
    dumped = inv.model_dump(mode="json")
    assert isinstance(dumped, dict)
    return dumped


async def _login(client: AsyncClient, db_session: AsyncSession, email: str) -> None:
    await client.post("/auth/signup", json={"email": email, "password": "supersecret123"})
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    user.email_verified = True
    user.email_verification_token = None
    await db_session.commit()
    await client.post("/auth/login", json={"email": email, "password": "supersecret123"})


async def _make_completed_invoice(
    db_session: AsyncSession, org_id: UUID, *, status: str = "completed",
    canonical: dict[str, Any] | None = None,
) -> Invoice:
    inv = Invoice(
        org_id=org_id,
        status=status,
        pdf_object_key=f"{org_id}/test.pdf",
        pdf_size_bytes=1234,
        pdf_sha256="b" * 64,
        original_filename="test.pdf",
        canonical_data=canonical if status == "completed" else None,
        extraction_path="haiku-only",
        extraction_model="claude-haiku-4-5",
        extraction_version="v1.1",
        overall_confidence=0.93,
        extracted_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    if status == "completed" and canonical is None:
        inv.canonical_data = _sample_canonical()
    db_session.add(inv)
    await db_session.commit()
    await db_session.refresh(inv)
    return inv


@pytest.mark.asyncio
async def test_export_json_happy_path(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _login(client, db_session, "exp@example.com")
    user = await db_session.scalar(select(User).where(User.email == "exp@example.com"))
    assert user is not None
    invoice = await _make_completed_invoice(db_session, user.org_id)

    resp = await client.get(f"/app/faktury/{invoice.id}/eksport/json")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert "attachment" in resp.headers["content-disposition"]
    assert "FV_01_2026_001.json" in resp.headers["content-disposition"]

    payload = json.loads(resp.content.decode("utf-8"))
    assert payload["invoice_number"] == "FV/01/2026/001"
    # Telemetry stripped
    assert "extracted_model" not in payload
    assert "overall_confidence" not in payload

    audit = await db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == "invoice.exported")
        .order_by(AuditEvent.created_at.desc())
    )
    assert audit is not None
    assert audit.payload["format"] == "json"
    assert audit.payload["invoice_id"] == str(invoice.id)


@pytest.mark.asyncio
async def test_export_csv_happy_path(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _login(client, db_session, "csv@example.com")
    user = await db_session.scalar(select(User).where(User.email == "csv@example.com"))
    assert user is not None
    invoice = await _make_completed_invoice(db_session, user.org_id)

    resp = await client.get(f"/app/faktury/{invoice.id}/eksport/csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "FV_01_2026_001.csv" in resp.headers["content-disposition"]

    raw = resp.content
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(raw[3:].decode("utf-8"))))
    assert len(rows) == 1
    assert rows[0]["Numer faktury"] == "FV/01/2026/001"


@pytest.mark.asyncio
async def test_export_rejects_unknown_format(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _login(client, db_session, "fmt@example.com")
    user = await db_session.scalar(select(User).where(User.email == "fmt@example.com"))
    assert user is not None
    invoice = await _make_completed_invoice(db_session, user.org_id)

    resp = await client.get(f"/app/faktury/{invoice.id}/eksport/xlsx")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_export_409_when_not_completed(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _login(client, db_session, "pend@example.com")
    user = await db_session.scalar(select(User).where(User.email == "pend@example.com"))
    assert user is not None
    invoice = await _make_completed_invoice(db_session, user.org_id, status="pending")

    resp = await client.get(f"/app/faktury/{invoice.id}/eksport/json")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_export_other_org_404(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _login(client, db_session, "owner3@example.com")
    owner = await db_session.scalar(select(User).where(User.email == "owner3@example.com"))
    assert owner is not None
    invoice = await _make_completed_invoice(db_session, owner.org_id)

    # Intruder logs in under a different org
    csrf_logout = await client.get("/auth/csrf")
    await client.post(
        "/auth/logout",
        headers={"X-CSRF-Token": str(csrf_logout.json()["csrf_token"])},
    )
    await _login(client, db_session, "intruder3@example.com")

    resp = await client.get(f"/app/faktury/{invoice.id}/eksport/json")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_jpk_fa_happy_path(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    """JPK_FA(4) export requires the org to have NIP + kod_urzedu set."""
    await _login(client, db_session, "jpk@example.com")
    user = await db_session.scalar(select(User).where(User.email == "jpk@example.com"))
    assert user is not None
    from src.models.org import Org
    org = await db_session.scalar(select(Org).where(Org.id == user.org_id))
    assert org is not None
    org.name = "Sprzedawca sp. z o.o."
    org.nip = "1234567819"
    org.kod_urzedu = "1471"
    await db_session.commit()

    invoice = await _make_completed_invoice(db_session, user.org_id)

    resp = await client.get(f"/app/faktury/{invoice.id}/eksport/jpk_fa")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/xml")
    assert "FV_01_2026_001.xml" in resp.headers["content-disposition"]
    body = resp.content
    assert body.startswith(b"<?xml")
    assert b"<JPK " in body or b"<JPK\n" in body
    assert b"JPK_FA (4)" in body
    assert b"<NIP>1234567819</NIP>" in body
    assert b"<KodUrzedu>1471</KodUrzedu>" in body


@pytest.mark.asyncio
async def test_export_fa3_happy_path(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    """FA(3) export doesn't need org settings — seller NIP from PDF only."""
    await _login(client, db_session, "fa3@example.com")
    user = await db_session.scalar(select(User).where(User.email == "fa3@example.com"))
    assert user is not None

    invoice = await _make_completed_invoice(db_session, user.org_id)

    resp = await client.get(f"/app/faktury/{invoice.id}/eksport/fa3")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/xml")
    assert ".fa3.xml" in resp.headers["content-disposition"]
    body = resp.content
    assert body.startswith(b"<?xml")
    assert b"<Faktura " in body or b"<Faktura\n" in body
    assert b"FA (3)" in body
    assert b"<WariantFormularza>3</WariantFormularza>" in body
    assert b"<Podmiot1>" in body
    assert b"<Podmiot2>" in body


@pytest.mark.asyncio
async def test_export_jpk_fa_422_when_kod_urzedu_missing(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _login(client, db_session, "noku@example.com")
    user = await db_session.scalar(select(User).where(User.email == "noku@example.com"))
    assert user is not None
    # Don't set kod_urzedu on the org.
    invoice = await _make_completed_invoice(db_session, user.org_id)

    resp = await client.get(f"/app/faktury/{invoice.id}/eksport/jpk_fa")
    assert resp.status_code == 422
    assert "kodu urzędu" in resp.json()["detail"].lower() or "urzędu" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_export_unauth_redirects_to_login(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    # Build invoice directly in DB without an active session.
    from src.models.org import Org
    org = Org(name="noauth-org")
    db_session.add(org)
    await db_session.flush()
    invoice = await _make_completed_invoice(db_session, org.id)

    resp = await client.get(
        f"/app/faktury/{invoice.id}/eksport/json",
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"

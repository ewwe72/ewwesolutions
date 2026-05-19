"""Integration tests for the review-page editing endpoints (Phase 4 chunk 3b).

POST /app/faktury/{id}/popraw — operator-typed JSON patch over an
already-extracted invoice. Pydantic validation + business validation
re-run server-side; user_reviewed_at + last_correction_at get stamped.

POST /app/faktury/{id}/ponow-ekstrakcje — re-runs extraction with Sonnet,
clearing any prior operator review.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api import invoices as invoices_module
from src.models.invoice import (
    CanonicalInvoice, Counterparty, Currency, InvoiceType,
    LineItem, Money, PaymentInfo, VATRate, VATSummaryEntry,
)
from src.models.invoice_record import Invoice
from src.models.user import User


def _pln(value: str) -> Money:
    return Money(amount=Decimal(value), currency=Currency.PLN)


def _sample_canonical() -> dict[str, Any]:
    """A clean, validating canonical invoice as a JSON-safe dict —
    mirrors what the worker writes into Invoice.canonical_data."""
    inv = CanonicalInvoice(
        invoice_number="FV/01/2026/001",
        invoice_type=InvoiceType.REGULAR,
        issue_date=date(2026, 5, 1),
        seller=Counterparty(
            name="Sprzedawca sp. z o.o.",
            nip="1234567819",
            confidence={"name": 0.95, "nip": 0.99},
        ),
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
        extraction_warnings=[],
        source_pdf_id="test.pdf",
        extracted_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
        extracted_model="claude-haiku-4-5",
        extraction_version="v1.0",
    )
    dumped = inv.model_dump(mode="json")
    assert isinstance(dumped, dict)
    return dumped


@pytest.fixture(autouse=True)
def _stub_storage_and_queue(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    enqueued: list[tuple[str, str | None]] = []

    class _FakeStorage:
        def put(self, key: str, content: bytes, content_type: str = "application/pdf") -> None:
            pass

        def get(self, key: str) -> bytes:
            return b"%PDF-1.4\n%%EOF\n"

    async def _fake_enqueue(invoice_id: UUID, force_model: str | None = None) -> None:
        enqueued.append((str(invoice_id), force_model))

    monkeypatch.setattr(invoices_module, "get_storage", lambda: _FakeStorage())
    monkeypatch.setattr(invoices_module, "_enqueue_extraction", _fake_enqueue)
    return {"enqueued": enqueued}


async def _login_with_csrf(
    client: AsyncClient, db_session: AsyncSession, email: str,
) -> str:
    """Sign up, verify, log in via JSON API, return a CSRF token bound to the session."""
    await client.post("/auth/signup", json={"email": email, "password": "supersecret123"})
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    user.email_verified = True
    user.email_verification_token = None
    await db_session.commit()
    await client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    csrf = await client.get("/auth/csrf")
    return str(csrf.json()["csrf_token"])


async def _make_completed_invoice(
    db_session: AsyncSession, org_id: UUID, *, canonical: dict[str, Any] | None = None,
) -> Invoice:
    inv = Invoice(
        org_id=org_id,
        status="completed",
        pdf_object_key=f"{org_id}/test.pdf",
        pdf_size_bytes=1234,
        pdf_sha256="a" * 64,
        original_filename="test.pdf",
        canonical_data=canonical or _sample_canonical(),
        extraction_path="haiku-only",
        extraction_model="claude-haiku-4-5",
        extraction_version="v1.0",
        overall_confidence=0.93,
        extracted_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    db_session.add(inv)
    await db_session.commit()
    await db_session.refresh(inv)
    return inv


@pytest.mark.asyncio
async def test_corrections_persists_and_stamps_review(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    csrf = await _login_with_csrf(client, db_session, "editor@example.com")
    user = await db_session.scalar(
        select(User).where(User.email == "editor@example.com")
    )
    assert user is not None
    invoice = await _make_completed_invoice(db_session, user.org_id)

    canonical = _sample_canonical()
    # Operator fixes a typo in the buyer name.
    canonical["buyer"]["name"] = "Nowy nabywca sp. z o.o."

    resp = await client.post(
        f"/app/faktury/{invoice.id}/popraw",
        data={"csrf_token": csrf, "canonical_json": json.dumps(canonical)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["redirect"] == f"/app/faktury/{invoice.id}"
    assert body["hard_warnings"] == 0

    await db_session.refresh(invoice)
    assert invoice.canonical_data is not None
    assert invoice.canonical_data["buyer"]["name"] == "Nowy nabywca sp. z o.o."
    assert invoice.user_reviewed_at is not None
    assert invoice.last_correction_at is not None
    # Metadata not user-editable → must be preserved.
    assert invoice.canonical_data["extracted_model"] == "claude-haiku-4-5"
    assert invoice.canonical_data["source_pdf_id"] == "test.pdf"


@pytest.mark.asyncio
async def test_corrections_rejects_metadata_overwrite(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    csrf = await _login_with_csrf(client, db_session, "tamper@example.com")
    user = await db_session.scalar(select(User).where(User.email == "tamper@example.com"))
    assert user is not None
    invoice = await _make_completed_invoice(db_session, user.org_id)

    # Try to lie about model + confidence — these should be ignored.
    payload = {
        **_sample_canonical(),
        "extracted_model": "MALICIOUS",
        "overall_confidence": 1.0,
        "source_pdf_id": "tampered.pdf",
    }
    resp = await client.post(
        f"/app/faktury/{invoice.id}/popraw",
        data={"csrf_token": csrf, "canonical_json": json.dumps(payload)},
    )
    assert resp.status_code == 200, resp.text
    await db_session.refresh(invoice)
    assert invoice.canonical_data is not None
    assert invoice.canonical_data["extracted_model"] == "claude-haiku-4-5"
    assert invoice.canonical_data["overall_confidence"] == 0.93
    assert invoice.canonical_data["source_pdf_id"] == "test.pdf"


@pytest.mark.asyncio
async def test_corrections_refreshes_warnings(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    csrf = await _login_with_csrf(client, db_session, "fixwarn@example.com")
    user = await db_session.scalar(select(User).where(User.email == "fixwarn@example.com"))
    assert user is not None

    # Seed with a known-broken invoice (totals off by 1.00 PLN — hard warning).
    broken = _sample_canonical()
    broken["total_gross"] = {"amount": "999.00", "currency": "PLN"}
    broken["extraction_warnings"] = ["sum(lines.gross)=123.00 ≠ total_gross=999.00"]
    invoice = await _make_completed_invoice(db_session, user.org_id, canonical=broken)

    # Operator fixes the total.
    fixed = _sample_canonical()  # totals match again
    resp = await client.post(
        f"/app/faktury/{invoice.id}/popraw",
        data={"csrf_token": csrf, "canonical_json": json.dumps(fixed)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["hard_warnings"] == 0
    await db_session.refresh(invoice)
    assert invoice.canonical_data is not None
    assert invoice.canonical_data["extraction_warnings"] == []


@pytest.mark.asyncio
async def test_corrections_returns_422_on_schema_failure(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    csrf = await _login_with_csrf(client, db_session, "bad@example.com")
    user = await db_session.scalar(select(User).where(User.email == "bad@example.com"))
    assert user is not None
    invoice = await _make_completed_invoice(db_session, user.org_id)

    bad = _sample_canonical()
    bad["issue_date"] = "not-a-date"

    resp = await client.post(
        f"/app/faktury/{invoice.id}/popraw",
        data={"csrf_token": csrf, "canonical_json": json.dumps(bad)},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert any("issue_date" in ".".join(str(p) for p in e.get("loc", [])) for e in body["errors"])

    # DB unchanged.
    await db_session.refresh(invoice)
    assert invoice.user_reviewed_at is None


@pytest.mark.asyncio
async def test_corrections_rejects_bad_json(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    csrf = await _login_with_csrf(client, db_session, "bj@example.com")
    user = await db_session.scalar(select(User).where(User.email == "bj@example.com"))
    assert user is not None
    invoice = await _make_completed_invoice(db_session, user.org_id)

    resp = await client.post(
        f"/app/faktury/{invoice.id}/popraw",
        data={"csrf_token": csrf, "canonical_json": "{ not json"},
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_corrections_csrf_required(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _login_with_csrf(client, db_session, "csrf@example.com")
    user = await db_session.scalar(select(User).where(User.email == "csrf@example.com"))
    assert user is not None
    invoice = await _make_completed_invoice(db_session, user.org_id)

    resp = await client.post(
        f"/app/faktury/{invoice.id}/popraw",
        data={"csrf_token": "bogus", "canonical_json": json.dumps(_sample_canonical())},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_corrections_other_org_404(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    # Owner uploads
    await _login_with_csrf(client, db_session, "owner2@example.com")
    owner = await db_session.scalar(select(User).where(User.email == "owner2@example.com"))
    assert owner is not None
    invoice = await _make_completed_invoice(db_session, owner.org_id)

    # Intruder logs in
    csrf_logout = await client.get("/auth/csrf")
    await client.post(
        "/auth/logout",
        headers={"X-CSRF-Token": str(csrf_logout.json()["csrf_token"])},
    )
    csrf = await _login_with_csrf(client, db_session, "intruder2@example.com")

    resp = await client.post(
        f"/app/faktury/{invoice.id}/popraw",
        data={"csrf_token": csrf, "canonical_json": json.dumps(_sample_canonical())},
    )
    assert resp.status_code == 404


async def _grant_balance(
    db_session: AsyncSession, org_id: UUID, grosze: int = 5000,
) -> None:
    """Top up org balance so the re-extract gate (email + balance) passes."""
    from src.models.org import Org
    org = await db_session.scalar(select(Org).where(Org.id == org_id))
    assert org is not None
    org.credit_balance_grosze = grosze
    await db_session.commit()


@pytest.mark.asyncio
async def test_reextract_enqueues_sonnet_and_resets_status(
    client: AsyncClient, db_session: AsyncSession,
    _stub_storage_and_queue: dict[str, list[Any]],
) -> None:
    csrf = await _login_with_csrf(client, db_session, "rx@example.com")
    user = await db_session.scalar(select(User).where(User.email == "rx@example.com"))
    assert user is not None
    await _grant_balance(db_session, user.org_id)
    invoice = await _make_completed_invoice(db_session, user.org_id)
    # Simulate prior operator review — should be wiped by re-extract worker
    # (worker side is unit-covered; here we verify the endpoint + enqueue).
    invoice.user_reviewed_at = datetime(2026, 5, 13, tzinfo=timezone.utc)
    await db_session.commit()

    resp = await client.post(
        f"/app/faktury/{invoice.id}/ponow-ekstrakcje",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/app/faktury/{invoice.id}"

    await db_session.refresh(invoice)
    assert invoice.status == "pending"
    assert invoice.extraction_error is None
    assert _stub_storage_and_queue["enqueued"] == [
        (str(invoice.id), "claude-sonnet-4-6")
    ]


@pytest.mark.asyncio
async def test_reextract_blocked_when_balance_empty(
    client: AsyncClient, db_session: AsyncSession,
    _stub_storage_and_queue: dict[str, list[Any]],
) -> None:
    """Without credit balance the re-extract endpoint must redirect to
    /app/billing — otherwise a user could spam Sonnet re-extracts and
    rack up an unbounded negative balance, since the worker debits but
    never refuses on negative."""
    csrf = await _login_with_csrf(client, db_session, "rx-noballance@example.com")
    user = await db_session.scalar(
        select(User).where(User.email == "rx-noballance@example.com")
    )
    assert user is not None
    invoice = await _make_completed_invoice(db_session, user.org_id)
    # Balance defaults to 0 from signup; do not top up.

    resp = await client.post(
        f"/app/faktury/{invoice.id}/ponow-ekstrakcje",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/app/billing?reason=empty"

    await db_session.refresh(invoice)
    # Status untouched, no job enqueued.
    assert invoice.status == "completed"
    assert _stub_storage_and_queue["enqueued"] == []


@pytest.mark.asyncio
async def test_reextract_blocked_when_email_unverified(
    client: AsyncClient, db_session: AsyncSession,
    _stub_storage_and_queue: dict[str, list[Any]],
) -> None:
    """Email-unverified users must not be able to spend balance via
    re-extract any more than they can via upload."""
    csrf = await _login_with_csrf(client, db_session, "rx-noemail@example.com")
    user = await db_session.scalar(
        select(User).where(User.email == "rx-noemail@example.com")
    )
    assert user is not None
    # Helper verifies email by default — unverify after the fact.
    user.email_verified = False
    await _grant_balance(db_session, user.org_id)
    await db_session.commit()
    invoice = await _make_completed_invoice(db_session, user.org_id)

    resp = await client.post(
        f"/app/faktury/{invoice.id}/ponow-ekstrakcje",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/app?reason=email_verify"

    await db_session.refresh(invoice)
    assert invoice.status == "completed"
    assert _stub_storage_and_queue["enqueued"] == []


@pytest.mark.asyncio
async def test_reextract_works_on_failed_status_invoice(
    client: AsyncClient, db_session: AsyncSession,
    _stub_storage_and_queue: dict[str, list[Any]],
) -> None:
    """The stub-page recovery button (added 2026-05-14 night) posts to the
    same endpoint as the review-page button, but the originating invoice
    status is `failed`, not `completed`. Verify the handler accepts that
    transition: status flips to pending, extraction_error clears, job
    enqueues with Sonnet."""
    csrf = await _login_with_csrf(client, db_session, "rxfail@example.com")
    user = await db_session.scalar(
        select(User).where(User.email == "rxfail@example.com")
    )
    assert user is not None
    await _grant_balance(db_session, user.org_id)
    invoice = await _make_completed_invoice(db_session, user.org_id)
    # Mimic a true initial-extraction failure: no canonical_data, status
    # 'failed', extraction_error set by the worker.
    invoice.status = "failed"
    invoice.canonical_data = None
    invoice.extraction_error = "BedrockAccessError: model access not granted"
    await db_session.commit()

    resp = await client.post(
        f"/app/faktury/{invoice.id}/ponow-ekstrakcje",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/app/faktury/{invoice.id}"

    await db_session.refresh(invoice)
    assert invoice.status == "pending"
    assert invoice.extraction_error is None
    assert _stub_storage_and_queue["enqueued"] == [
        (str(invoice.id), "claude-sonnet-4-6")
    ]


@pytest.mark.asyncio
async def test_corrections_accepts_added_line(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    """Chunk 3b' add-row: the UI lets the operator append a missed line
    item. The serialiser posts a longer `lines` array; the server must
    accept it and persist the new entry."""
    csrf = await _login_with_csrf(client, db_session, "addline@example.com")
    user = await db_session.scalar(select(User).where(User.email == "addline@example.com"))
    assert user is not None
    invoice = await _make_completed_invoice(db_session, user.org_id)

    canonical = _sample_canonical()
    # New line, mirrors the JS clone defaults: 1 unit, 23% VAT, all-zero amounts
    # for the user to fill in after add. Then the operator fills real values
    # before save — here we simulate the post-fill state.
    canonical["lines"].append({
        "line_no": 2,
        "description": "Doliczona pozycja",
        "quantity": "2",
        "unit": "szt.",
        "unit_price_net": {"amount": "50.00", "currency": "PLN"},
        "vat_rate": "23",
        "discount_pct": "0",
        "net_value": {"amount": "100.00", "currency": "PLN"},
        "vat_value": {"amount": "23.00", "currency": "PLN"},
        "gross_value": {"amount": "123.00", "currency": "PLN"},
    })
    # Bump totals so lines sum matches (no hard warnings).
    canonical["total_net"] = {"amount": "200.00", "currency": "PLN"}
    canonical["total_vat"] = {"amount": "46.00", "currency": "PLN"}
    canonical["total_gross"] = {"amount": "246.00", "currency": "PLN"}
    canonical["vat_summary"][0] = {
        "rate": "23",
        "net_total": {"amount": "200.00", "currency": "PLN"},
        "vat_total": {"amount": "46.00", "currency": "PLN"},
        "gross_total": {"amount": "246.00", "currency": "PLN"},
    }

    resp = await client.post(
        f"/app/faktury/{invoice.id}/popraw",
        data={"csrf_token": csrf, "canonical_json": json.dumps(canonical)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["hard_warnings"] == 0

    await db_session.refresh(invoice)
    assert invoice.canonical_data is not None
    lines = invoice.canonical_data["lines"]
    assert len(lines) == 2
    assert lines[1]["description"] == "Doliczona pozycja"
    assert lines[1]["gross_value"]["amount"] == "123.00"


@pytest.mark.asyncio
async def test_corrections_accepts_added_vat_summary_entry(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    """Add-row for VAT summary: operator appends a missed VAT rate
    (e.g. mixed 23% + 8% invoice where extraction only caught one)."""
    csrf = await _login_with_csrf(client, db_session, "addvat@example.com")
    user = await db_session.scalar(select(User).where(User.email == "addvat@example.com"))
    assert user is not None
    invoice = await _make_completed_invoice(db_session, user.org_id)

    canonical = _sample_canonical()
    # Add a second VAT bucket at 8% — gross totals stay coherent because
    # we also add a matching line below it.
    canonical["lines"].append({
        "line_no": 2,
        "description": "Pozycja 8%",
        "quantity": "1",
        "unit": "szt.",
        "unit_price_net": {"amount": "100.00", "currency": "PLN"},
        "vat_rate": "8",
        "discount_pct": "0",
        "net_value": {"amount": "100.00", "currency": "PLN"},
        "vat_value": {"amount": "8.00", "currency": "PLN"},
        "gross_value": {"amount": "108.00", "currency": "PLN"},
    })
    canonical["vat_summary"].append({
        "rate": "8",
        "net_total": {"amount": "100.00", "currency": "PLN"},
        "vat_total": {"amount": "8.00", "currency": "PLN"},
        "gross_total": {"amount": "108.00", "currency": "PLN"},
    })
    canonical["total_net"] = {"amount": "200.00", "currency": "PLN"}
    canonical["total_vat"] = {"amount": "31.00", "currency": "PLN"}
    canonical["total_gross"] = {"amount": "231.00", "currency": "PLN"}

    resp = await client.post(
        f"/app/faktury/{invoice.id}/popraw",
        data={"csrf_token": csrf, "canonical_json": json.dumps(canonical)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["hard_warnings"] == 0

    await db_session.refresh(invoice)
    assert invoice.canonical_data is not None
    vat_rows = invoice.canonical_data["vat_summary"]
    assert len(vat_rows) == 2
    assert {row["rate"] for row in vat_rows} == {"23", "8"}


@pytest.mark.asyncio
async def test_reextract_skips_when_already_processing(
    client: AsyncClient, db_session: AsyncSession,
    _stub_storage_and_queue: dict[str, list[Any]],
) -> None:
    csrf = await _login_with_csrf(client, db_session, "rx2@example.com")
    user = await db_session.scalar(select(User).where(User.email == "rx2@example.com"))
    assert user is not None
    await _grant_balance(db_session, user.org_id)
    invoice = await _make_completed_invoice(db_session, user.org_id)
    invoice.status = "processing"
    await db_session.commit()

    resp = await client.post(
        f"/app/faktury/{invoice.id}/ponow-ekstrakcje",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert _stub_storage_and_queue["enqueued"] == []

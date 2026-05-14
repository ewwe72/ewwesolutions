"""Unit tests for JSON + CSV exporters.

Lives under tests/unit/ — no DB, no HTTP. The endpoint wiring is covered
in tests/integration/test_export.py.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timezone
from decimal import Decimal

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
from src.pipeline.export import csv_export, json_export


def _pln(value: str) -> Money:
    return Money(amount=Decimal(value), currency=Currency.PLN)


def _invoice() -> CanonicalInvoice:
    return CanonicalInvoice(
        invoice_number="FV/01/2026/001",
        invoice_type=InvoiceType.REGULAR,
        issue_date=date(2026, 5, 1),
        sale_date=date(2026, 5, 1),
        seller=Counterparty(
            name="Sprzedawca sp. z o.o.",
            nip="1234567819",
            confidence={"name": 0.95, "nip": 0.99},
        ),
        buyer=Counterparty(
            name="Nabywca sp. z o.o.",
            nip="1234567819",
            confidence={"name": 0.90},
        ),
        lines=[
            LineItem(
                line_no=1,
                description="Konsultacja techniczna",
                quantity=Decimal("2"),
                unit="godz.",
                unit_price_net=_pln("50.00"),
                vat_rate=VATRate.R23,
                net_value=_pln("100.00"),
                vat_value=_pln("23.00"),
                gross_value=_pln("123.00"),
                confidence={"description": 0.88},
            ),
        ],
        vat_summary=[
            VATSummaryEntry(
                rate=VATRate.R23,
                net_total=_pln("100.00"),
                vat_total=_pln("23.00"),
                gross_total=_pln("123.00"),
            ),
        ],
        total_net=_pln("100.00"),
        total_vat=_pln("23.00"),
        total_gross=_pln("123.00"),
        payment=PaymentInfo(method="przelew", due_date=date(2026, 5, 15)),
        overall_confidence=0.93,
        extraction_warnings=["(soft) some warning"],
        source_pdf_id="test.pdf",
        extracted_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
        extracted_model="claude-haiku-4-5",
        extraction_version="v1.1",
    )


def test_json_export_strips_telemetry() -> None:
    payload = json.loads(json_export.to_bytes(_invoice()).decode("utf-8"))
    for stripped in (
        "overall_confidence",
        "extraction_warnings",
        "source_pdf_id",
        "extracted_at",
        "extracted_model",
        "extraction_version",
    ):
        assert stripped not in payload, f"{stripped} should be removed from export"


def test_json_export_strips_confidence_dicts() -> None:
    payload = json.loads(json_export.to_bytes(_invoice()).decode("utf-8"))
    assert "confidence" not in payload["seller"]
    assert "confidence" not in payload["buyer"]
    assert all("confidence" not in line for line in payload["lines"])


def test_json_export_preserves_canonical_fields() -> None:
    payload = json.loads(json_export.to_bytes(_invoice()).decode("utf-8"))
    assert payload["invoice_number"] == "FV/01/2026/001"
    assert payload["seller"]["nip"] == "1234567819"
    assert payload["total_gross"] == {"amount": "123.00", "currency": "PLN"}
    assert payload["lines"][0]["description"] == "Konsultacja techniczna"


def test_csv_export_has_bom_and_polish_header() -> None:
    raw = csv_export.to_bytes(_invoice())
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV should be UTF-8 BOM for Excel"
    text = raw[3:].decode("utf-8")
    header = text.splitlines()[0]
    assert "Numer faktury" in header
    assert "Wartość netto" in header  # Polish chars survive encoding


def test_csv_export_one_row_per_line_item() -> None:
    raw = csv_export.to_bytes(_invoice())
    reader = csv.DictReader(io.StringIO(raw[3:].decode("utf-8")))
    rows = list(reader)
    assert len(rows) == 1  # only one line item in fixture
    row = rows[0]
    assert row["Numer faktury"] == "FV/01/2026/001"
    assert row["Sprzedawca"] == "Sprzedawca sp. z o.o."
    assert row["Opis"] == "Konsultacja techniczna"
    assert row["Stawka VAT"] == "23"
    assert row["Wartość brutto"] == "123.00"
    assert row["Razem brutto (faktura)"] == "123.00"
    assert row["Waluta"] == "PLN"

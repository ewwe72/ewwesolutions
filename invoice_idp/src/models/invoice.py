"""Canonical Invoice schema — the contract between extraction and export."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field


class InvoiceType(str, Enum):
    REGULAR = "VAT"
    PRO_FORMA = "PROFORMA"
    CORRECTION = "KOREKTA"
    DUPLICATE = "DUPLIKAT"
    SIMPLIFIED = "UPROSZCZONA"
    RECEIPT = "PARAGON"


class Currency(str, Enum):
    PLN = "PLN"
    EUR = "EUR"
    USD = "USD"
    GBP = "GBP"
    CHF = "CHF"
    CZK = "CZK"


class VATRate(str, Enum):
    """Polish VAT rate codes — must match JPK_FA enum values."""
    R23 = "23"
    R8 = "8"
    R5 = "5"
    R0 = "0"
    ZW = "zw"
    NP = "np"
    OO = "oo"


class Money(BaseModel):
    amount: Annotated[Decimal, Field(max_digits=14, decimal_places=2)]
    currency: Currency


class Counterparty(BaseModel):
    name: str
    nip: str | None = None
    regon: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str = "PL"
    bank_account: str | None = None
    confidence: dict[str, float] = Field(default_factory=dict)


class LineItem(BaseModel):
    line_no: int
    description: str
    quantity: Annotated[Decimal, Field(max_digits=12, decimal_places=4)]
    unit: str = "szt."
    unit_price_net: Money
    vat_rate: VATRate
    discount_pct: Annotated[Decimal, Field(max_digits=5, decimal_places=2)] = Decimal(0)
    net_value: Money
    vat_value: Money
    gross_value: Money
    confidence: dict[str, float] = Field(default_factory=dict)


class VATSummaryEntry(BaseModel):
    rate: VATRate
    net_total: Money
    vat_total: Money
    gross_total: Money


class PaymentInfo(BaseModel):
    method: str | None = None
    due_date: date | None = None
    paid: bool = False
    paid_date: date | None = None
    bank_account: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CanonicalInvoice(BaseModel):
    """Single source of truth: populated by extraction, consumed by exports."""

    invoice_number: str
    invoice_type: InvoiceType = InvoiceType.REGULAR
    issue_date: date
    sale_date: date | None = None
    place_of_issue: str | None = None

    seller: Counterparty
    buyer: Counterparty

    lines: list[LineItem]
    vat_summary: list[VATSummaryEntry]

    total_net: Money
    total_vat: Money
    total_gross: Money

    payment: PaymentInfo = Field(default_factory=PaymentInfo)

    notes: str | None = None

    overall_confidence: float = 0.0
    extraction_warnings: list[str] = Field(default_factory=list)
    source_pdf_id: str = ""
    extracted_at: datetime = Field(default_factory=_utcnow)
    extracted_model: str = ""
    extraction_version: str = ""

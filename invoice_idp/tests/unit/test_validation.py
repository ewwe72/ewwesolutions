"""Validation layer: VAT math, totals, NIP/currency checks."""

from __future__ import annotations

from datetime import date
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
from src.pipeline.validation.checks import compute_overall_confidence, validate


def _pln(value: str) -> Money:
    return Money(amount=Decimal(value), currency=Currency.PLN)


def _make_clean_invoice() -> CanonicalInvoice:
    """A minimal but fully-reconciling invoice — should produce zero hard warnings."""
    line = LineItem(
        line_no=1,
        description="Konsultacja",
        quantity=Decimal("1"),
        unit="godz.",
        unit_price_net=_pln("100.00"),
        vat_rate=VATRate.R23,
        net_value=_pln("100.00"),
        vat_value=_pln("23.00"),
        gross_value=_pln("123.00"),
    )
    return CanonicalInvoice(
        invoice_number="FV/01/2026/001",
        invoice_type=InvoiceType.REGULAR,
        issue_date=date(2026, 5, 1),
        seller=Counterparty(name="Sprzedawca sp. z o.o.", nip="1234567819"),
        buyer=Counterparty(name="Nabywca sp. z o.o.", nip="1234567819"),
        lines=[line],
        vat_summary=[
            VATSummaryEntry(
                rate=VATRate.R23,
                net_total=_pln("100.00"),
                vat_total=_pln("23.00"),
                gross_total=_pln("123.00"),
            )
        ],
        total_net=_pln("100.00"),
        total_vat=_pln("23.00"),
        total_gross=_pln("123.00"),
        payment=PaymentInfo(due_date=date(2026, 5, 15)),
    )


def test_clean_invoice_has_no_hard_warnings() -> None:
    hard, soft = validate(_make_clean_invoice())
    assert hard == []
    assert soft == []


def test_detects_line_vat_math_off() -> None:
    inv = _make_clean_invoice()
    # Break line vat_value: should be 23.00, set to 22.00
    inv.lines[0] = inv.lines[0].model_copy(update={"vat_value": _pln("22.00"), "gross_value": _pln("122.00")})
    # Also update totals so the "sum(lines) vs totals" check doesn't masquerade
    inv = inv.model_copy(update={
        "total_vat": _pln("22.00"),
        "total_gross": _pln("122.00"),
    })
    hard, _ = validate(inv)
    assert any("VAT math off" in w for w in hard)


def test_detects_net_plus_vat_not_gross() -> None:
    inv = _make_clean_invoice()
    inv.lines[0] = inv.lines[0].model_copy(update={"gross_value": _pln("130.00")})
    inv = inv.model_copy(update={"total_gross": _pln("130.00")})
    hard, _ = validate(inv)
    assert any("net+vat≠gross" in w for w in hard)


def test_detects_totals_mismatch() -> None:
    inv = _make_clean_invoice()
    inv = inv.model_copy(update={"total_net": _pln("999.00")})
    hard, _ = validate(inv)
    assert any("total_net" in w for w in hard)


def test_invalid_seller_nip_is_hard() -> None:
    inv = _make_clean_invoice()
    inv = inv.model_copy(update={
        "seller": inv.seller.model_copy(update={"nip": "1234567890"})
    })
    hard, _ = validate(inv)
    assert any("seller.nip checksum invalid" in w for w in hard)


def test_missing_seller_nip_for_VAT_is_hard() -> None:
    inv = _make_clean_invoice()
    inv = inv.model_copy(update={"seller": inv.seller.model_copy(update={"nip": None})})
    hard, _ = validate(inv)
    assert any("seller.nip required" in w for w in hard)


def test_due_before_issue_is_soft() -> None:
    inv = _make_clean_invoice()
    inv = inv.model_copy(update={
        "payment": inv.payment.model_copy(update={"due_date": date(2026, 4, 1)})
    })
    hard, soft = validate(inv)
    assert hard == []
    assert any("due_date" in w for w in soft)


def test_mixed_currencies_is_hard() -> None:
    inv = _make_clean_invoice()
    inv = inv.model_copy(update={
        "total_vat": Money(amount=Decimal("23.00"), currency=Currency.EUR),
    })
    hard, _ = validate(inv)
    assert any("mixed currencies" in w for w in hard)


def test_overall_confidence_applies_penalties() -> None:
    base = compute_overall_confidence({"a": 1.0, "b": 1.0}, hard_count=0, soft_count=0)
    assert base == 1.0
    penalised = compute_overall_confidence({"a": 1.0, "b": 1.0}, hard_count=2, soft_count=1)
    # 1.0 − 2*0.10 − 1*0.03 = 0.77
    assert abs(penalised - 0.77) < 1e-9
    floored = compute_overall_confidence({"a": 0.0}, hard_count=10, soft_count=10)
    assert floored == 0.0


def test_overall_confidence_neutral_when_no_self_scores() -> None:
    # No self-confidence reported → fall back to neutral 0.5
    assert compute_overall_confidence({}, 0, 0) == 0.5

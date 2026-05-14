"""Unit tests for JPK_FA(4) XML exporter."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from lxml import etree

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
from src.pipeline.export import jpk_fa
from src.pipeline.export.jpk_fa import (
    JPK_FA_NAMESPACE,
    JpkFaExportError,
    build_jpk_fa,
    to_bytes,
)

_NS = {"jpk": JPK_FA_NAMESPACE}


def _pln(value: str) -> Money:
    return Money(amount=Decimal(value), currency=Currency.PLN)


def _invoice(
    *,
    seller_nip: str | None = "1234567819",
    invoice_type: InvoiceType = InvoiceType.REGULAR,
) -> CanonicalInvoice:
    return CanonicalInvoice(
        invoice_number="FV/01/2026/001",
        invoice_type=invoice_type,
        issue_date=date(2026, 5, 1),
        sale_date=date(2026, 5, 1),
        seller=Counterparty(
            name="Sprzedawca sp. z o.o.",
            nip=seller_nip,
            address_line1="ul. Testowa 1",
            postal_code="00-001",
            city="Warszawa",
        ),
        buyer=Counterparty(
            name="Nabywca sp. z o.o.",
            nip="1234567819",
            address_line1="ul. Kupiecka 2",
            city="Kraków",
        ),
        lines=[LineItem(
            line_no=1,
            description="Konsultacja techniczna",
            quantity=Decimal("2"),
            unit="godz.",
            unit_price_net=_pln("50.00"),
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
    )


def _build(**kw: Any) -> etree._Element:
    return build_jpk_fa(
        kw.pop("invoice", _invoice()),
        org_name=kw.pop("org_name", "Sprzedawca sp. z o.o."),
        org_nip=kw.pop("org_nip", "1234567819"),
        org_kod_urzedu=kw.pop("org_kod_urzedu", "1471"),
        generated_at=datetime(2026, 5, 13, 15, 35, tzinfo=timezone.utc),
    )


def _text(root: etree._Element, xpath: str) -> str:
    el = root.find(xpath, _NS)
    assert el is not None, f"missing element: {xpath}"
    return str(el.text)


def test_naglowek_fixed_attributes() -> None:
    root = _build()
    kf = root.find(".//jpk:KodFormularza", _NS)
    assert kf is not None and kf.text == "JPK_FA"
    assert kf.get("kodSystemowy") == "JPK_FA (4)"
    assert kf.get("wersjaSchemy") == "1-0"
    assert _text(root, ".//jpk:WariantFormularza") == "4"
    assert _text(root, ".//jpk:NazwaSystemu") == "Faktomat"
    assert _text(root, ".//jpk:KodUrzedu") == "1471"


def test_date_range_covers_issue_month() -> None:
    root = _build()
    assert _text(root, ".//jpk:DataOd") == "2026-05-01"
    assert _text(root, ".//jpk:DataDo") == "2026-05-31"


def test_podmiot1_uses_seller_when_invoice_has_nip() -> None:
    root = _build()
    assert _text(root, ".//jpk:Podmiot1/jpk:IdentyfikatorPodmiotu/jpk:NIP") == "1234567819"
    assert _text(root, ".//jpk:AdresPodmiotu/jpk:KodKraju") == "PL"


def test_podmiot1_falls_back_to_org_nip_when_seller_blank() -> None:
    inv = _invoice(seller_nip=None)
    root = _build(invoice=inv, org_nip="9999999999")
    assert _text(root, ".//jpk:Podmiot1//jpk:NIP") == "9999999999"


def test_faktura_fields_populated() -> None:
    root = _build()
    fak = root.find(".//jpk:Faktura", _NS)
    assert fak is not None
    assert fak.get("typ") == "G"
    assert _text(fak, "jpk:KodWaluty") == "PLN"
    assert _text(fak, "jpk:P_1") == "2026-05-01"
    assert _text(fak, "jpk:P_2A") == "FV/01/2026/001"
    assert _text(fak, "jpk:P_4B") == "1234567819"
    assert _text(fak, "jpk:P_5B") == "1234567819"
    assert _text(fak, "jpk:P_13_1") == "100.00"
    assert _text(fak, "jpk:P_14_1") == "23.00"
    assert _text(fak, "jpk:P_15") == "123.00"
    assert _text(fak, "jpk:RodzajFaktury") == "VAT"


def test_invoice_type_maps_to_rodzaj_faktury() -> None:
    for inv_type, expected in [
        (InvoiceType.REGULAR, "VAT"),
        (InvoiceType.CORRECTION, "KOREKTA"),
        (InvoiceType.PRO_FORMA, "POZ"),
    ]:
        root = _build(invoice=_invoice(invoice_type=inv_type))
        assert _text(root, ".//jpk:RodzajFaktury") == expected


def test_faktura_wiersze_per_line() -> None:
    root = _build()
    wiersze = root.findall(".//jpk:FakturaWiersz", _NS)
    assert len(wiersze) == 1
    w = wiersze[0]
    assert w.get("typ") == "G"
    assert _text(w, "jpk:P_2B") == "FV/01/2026/001"
    assert _text(w, "jpk:P_7") == "Konsultacja techniczna"
    assert _text(w, "jpk:P_8A") == "godz."
    assert _text(w, "jpk:P_8B") == "2.0000"
    assert _text(w, "jpk:P_11") == "100.00"
    assert _text(w, "jpk:P_12") == "23"


def test_faktura_ctrl_and_wiersz_ctrl_match_counts() -> None:
    root = _build()
    assert _text(root, ".//jpk:FakturaCtrl/jpk:LiczbaFaktur") == "1"
    assert _text(root, ".//jpk:FakturaCtrl/jpk:WartoscFaktur") == "123.00"
    assert _text(root, ".//jpk:FakturaWierszCtrl/jpk:LiczbaWierszyFaktur") == "1"
    assert _text(root, ".//jpk:FakturaWierszCtrl/jpk:WartoscWierszyFaktur") == "100.00"


def test_export_refuses_when_kod_urzedu_missing() -> None:
    with pytest.raises(JpkFaExportError, match="kodu urzędu"):
        _build(org_kod_urzedu=None)


def test_export_refuses_when_nip_missing_everywhere() -> None:
    inv = _invoice(seller_nip=None)
    with pytest.raises(JpkFaExportError, match="NIP"):
        _build(invoice=inv, org_nip=None)


def test_to_bytes_is_well_formed_xml_with_declaration() -> None:
    xml = to_bytes(
        _invoice(),
        org_name="Sprzedawca sp. z o.o.",
        org_nip="1234567819",
        org_kod_urzedu="1471",
    )
    assert xml.startswith(b"<?xml")
    assert b"UTF-8" in xml
    parsed = etree.fromstring(xml)
    assert parsed.tag == f"{{{JPK_FA_NAMESPACE}}}JPK"


def test_validate_xsd_returns_empty_when_no_schema_present() -> None:
    # The XSD is gitignored by design — the fallback is no-op.
    assert jpk_fa.validate_xsd(b"<JPK/>") == []

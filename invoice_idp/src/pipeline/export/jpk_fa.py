"""JPK_FA(4) XML exporter — Polish invoice register for tax-office filing.

The differentiator vs Parseur / international IDP tools. The XSD lives at
`schemas/jpk_fa_v4.xsd` (download per `schemas/README.md`); the exporter
runs without it, doing Python-level structural validation instead.
If the XSD is dropped in, `validate_xsd(xml_bytes)` activates and
returns the lxml-reported error list.

Scope: **single-invoice exports**. Batch JPK_FA (one filing covering a
whole month) is V1.x — the structure already supports multiple
`<Faktura>` siblings, but the endpoint and UI are wired for one
invoice at a time today.

Per SPEC §7.1: the XSD is source-of-truth; the spec's illustrative
example may contain stale field meanings. The element names below
(P_1, P_2A, P_3A-P_5B, P_13_..., P_15, RodzajFaktury) are the
Ministerstwo Finansów canonical field IDs.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from lxml import etree

from src.models.invoice import (
    CanonicalInvoice,
    InvoiceType,
    VATRate,
    VATSummaryEntry,
)

# Namespace per SPEC §7.1. Operator-downloaded XSD may carry a different
# `targetNamespace` — if so, update this constant + the README pointer.
JPK_FA_NAMESPACE = "http://crd.gov.pl/wzor/2022/03/03/11455/"
JPK_FA_XSD_PATH = Path(__file__).resolve().parents[3] / "schemas" / "jpk_fa_v4.xsd"

# Map CanonicalInvoice.invoice_type → JPK_FA RodzajFaktury enum.
# JPK_FA(4) uses: VAT, KOREKTA, ZAL (zaliczkowa), POZ (pozostałe).
_INVOICE_TYPE_TO_RODZAJ: dict[InvoiceType, str] = {
    InvoiceType.REGULAR: "VAT",
    InvoiceType.CORRECTION: "KOREKTA",
    InvoiceType.PRO_FORMA: "POZ",
    InvoiceType.DUPLICATE: "VAT",
    InvoiceType.SIMPLIFIED: "VAT",
    InvoiceType.RECEIPT: "VAT",
}

# Per-rate aggregation buckets in <Faktura>.
# P_13_x = sum of net at rate, P_14_x = sum of VAT at rate.
# Numbered indices: 1=23%, 2=8%, 3=5%, 4=0% (krajowa), 6=ZW, 7=NP.
_RATE_TO_P_INDEX: dict[VATRate, str] = {
    VATRate.R23: "1",
    VATRate.R8: "2",
    VATRate.R5: "3",
    VATRate.R0: "4",
    VATRate.ZW: "7",
    VATRate.NP: "6",
    VATRate.OO: "6",
}


class JpkFaExportError(ValueError):
    """Raised when the canonical invoice can't be serialised to JPK_FA(4)."""


def _money(d: Decimal) -> str:
    return f"{d.quantize(Decimal('0.01'))}"


def _q4(d: Decimal) -> str:
    """4-decimal-place quantity per JPK_FA TKwotowy variant."""
    return f"{d.quantize(Decimal('0.0001'))}"


def _check_export_ready(
    invoice: CanonicalInvoice,
    *,
    org_nip: str | None,
    org_kod_urzedu: str | None,
) -> None:
    """Raise JpkFaExportError if anything required by JPK_FA is missing.

    These are *hard* requirements — JPK_FA filings without them are
    rejected by the Ministry's PUE portal, so refuse upfront with a
    clear message instead of producing an invalid file.
    """
    problems: list[str] = []

    if not (invoice.seller.nip or org_nip):
        problems.append(
            "Brak NIP sprzedawcy (Podmiot1) — uzupełnij w danych klienta lub "
            "w ustawieniach organizacji."
        )
    if not org_kod_urzedu:
        problems.append(
            "Brak kodu urzędu skarbowego — uzupełnij w ustawieniach "
            "organizacji (Settings → Kod urzędu)."
        )
    if not invoice.seller.name:
        problems.append("Brak nazwy sprzedawcy.")
    if not invoice.lines:
        problems.append("Faktura nie zawiera pozycji.")
    if not invoice.vat_summary:
        problems.append("Brak podsumowania VAT.")

    if problems:
        raise JpkFaExportError("; ".join(problems))


def _aggregate_per_rate(
    summary: list[VATSummaryEntry],
) -> dict[VATRate, tuple[Decimal, Decimal]]:
    """Collapse the per-rate summary into one (net, vat) tuple per rate."""
    agg: dict[VATRate, tuple[Decimal, Decimal]] = {}
    for entry in summary:
        net = entry.net_total.amount
        vat = entry.vat_total.amount
        existing_net, existing_vat = agg.get(entry.rate, (Decimal(0), Decimal(0)))
        agg[entry.rate] = (existing_net + net, existing_vat + vat)
    return agg


def build_jpk_fa(
    invoice: CanonicalInvoice,
    *,
    org_name: str,
    org_nip: str | None,
    org_kod_urzedu: str | None,
    cel_zlozenia: int = 1,
    generated_at: datetime | None = None,
) -> etree._Element:
    """Build the JPK_FA(4) element tree for a single canonical invoice.

    `org_*` arguments carry the **filer** data (per SPEC §7.1: for
    sales-side JPK_FA, the filer is the seller). For an accounting
    office filing on behalf of clients, the org represents the client.

    `cel_zlozenia`: 1 = złożenie (original); 2 = korekta (correction).
    """
    _check_export_ready(invoice, org_nip=org_nip, org_kod_urzedu=org_kod_urzedu)
    generated_at = generated_at or datetime.now(timezone.utc).replace(microsecond=0)
    nip = invoice.seller.nip or org_nip
    assert nip is not None  # guaranteed by _check_export_ready
    assert org_kod_urzedu is not None

    ns = JPK_FA_NAMESPACE
    nsmap = {None: ns}
    root = etree.Element(f"{{{ns}}}JPK", nsmap=nsmap)

    _build_naglowek(
        root, ns, invoice=invoice, generated_at=generated_at,
        cel_zlozenia=cel_zlozenia, kod_urzedu=org_kod_urzedu,
    )
    _build_podmiot1(root, ns, invoice=invoice, nip=nip, name=org_name)
    _build_faktura(root, ns, invoice=invoice)
    _build_faktura_ctrl(root, ns, invoice=invoice)
    _build_faktura_wiersze(root, ns, invoice=invoice)
    _build_faktura_wiersz_ctrl(root, ns, invoice=invoice)

    return root


def _sub(parent: etree._Element, ns: str, tag: str, text: str | None = None,
         **attrs: str) -> etree._Element:
    el = etree.SubElement(parent, f"{{{ns}}}{tag}")
    if text is not None:
        el.text = text
    for k, v in attrs.items():
        el.set(k, v)
    return el


def _build_naglowek(
    root: etree._Element, ns: str, *,
    invoice: CanonicalInvoice, generated_at: datetime,
    cel_zlozenia: int, kod_urzedu: str,
) -> None:
    nag = _sub(root, ns, "Naglowek")
    kf = _sub(nag, ns, "KodFormularza", "JPK_FA")
    kf.set("kodSystemowy", "JPK_FA (4)")
    kf.set("wersjaSchemy", "1-0")
    _sub(nag, ns, "WariantFormularza", "4")
    _sub(nag, ns, "DataWytworzeniaJPK", generated_at.strftime("%Y-%m-%dT%H:%M:%S"))

    range_start, range_end = _date_range_for(invoice.issue_date)
    _sub(nag, ns, "DataOd", range_start.isoformat())
    _sub(nag, ns, "DataDo", range_end.isoformat())
    _sub(nag, ns, "NazwaSystemu", "Faktomat")
    _sub(nag, ns, "CelZlozenia", str(cel_zlozenia))
    _sub(nag, ns, "KodUrzedu", kod_urzedu)


def _date_range_for(issue_date: date) -> tuple[date, date]:
    """Default JPK_FA date range = month containing the issue date.

    Single-invoice export uses the whole month so the file is a valid
    monthly JPK_FA filing — the operator can later swap to a tighter
    range or batch multiple invoices into one filing.
    """
    if issue_date.month == 12:
        next_month_first = date(issue_date.year + 1, 1, 1)
    else:
        next_month_first = date(issue_date.year, issue_date.month + 1, 1)
    last_of_month = date.fromordinal(next_month_first.toordinal() - 1)
    first_of_month = issue_date.replace(day=1)
    return first_of_month, last_of_month


def _build_podmiot1(
    root: etree._Element, ns: str, *,
    invoice: CanonicalInvoice, nip: str, name: str,
) -> None:
    pod = _sub(root, ns, "Podmiot1")
    idp = _sub(pod, ns, "IdentyfikatorPodmiotu")
    _sub(idp, ns, "NIP", nip)
    _sub(idp, ns, "PelnaNazwa", name)
    addr = _sub(pod, ns, "AdresPodmiotu")
    seller = invoice.seller
    _sub(addr, ns, "KodKraju", seller.country or "PL")
    # Simple address form: AdresL1 / AdresL2 (JPK_FA accepts this as an
    # alternative to the structured Wojewodztwo/Powiat/Gmina breakdown).
    # If the operator's clients need structured addresses, that's a
    # CanonicalInvoice.Counterparty schema extension.
    if seller.address_line1:
        _sub(addr, ns, "AdresL1", seller.address_line1)
    if seller.address_line2 or seller.postal_code or seller.city:
        line2 = " ".join(p for p in (
            seller.postal_code, seller.city, seller.address_line2,
        ) if p)
        _sub(addr, ns, "AdresL2", line2)


def _build_faktura(
    root: etree._Element, ns: str, *, invoice: CanonicalInvoice,
) -> None:
    fak = _sub(root, ns, "Faktura", typ="G")
    _sub(fak, ns, "KodWaluty", invoice.total_net.currency.value)
    _sub(fak, ns, "P_1", invoice.issue_date.isoformat())
    _sub(fak, ns, "P_2A", invoice.invoice_number)

    # P_3A/3B = buyer name + address; P_3C/3D = seller name + address.
    # (Ministry numbering: 3 = nabywca, 3C-D add seller for the
    # "compact" form when AdresL1/L2 is used in Podmiot1.)
    buyer = invoice.buyer
    seller = invoice.seller
    _sub(fak, ns, "P_3A", buyer.name)
    _sub(fak, ns, "P_3B", _format_address(buyer))
    _sub(fak, ns, "P_3C", seller.name)
    _sub(fak, ns, "P_3D", _format_address(seller))

    if seller.nip:
        _sub(fak, ns, "P_4A", seller.country or "PL")
        _sub(fak, ns, "P_4B", seller.nip)
    if buyer.nip:
        _sub(fak, ns, "P_5A", buyer.country or "PL")
        _sub(fak, ns, "P_5B", buyer.nip)

    if invoice.sale_date and invoice.sale_date != invoice.issue_date:
        _sub(fak, ns, "P_6", invoice.sale_date.isoformat())

    # Per-rate totals: P_13_1/P_14_1 = net+VAT @ 23%, etc.
    per_rate = _aggregate_per_rate(invoice.vat_summary)
    for rate, (net, vat) in per_rate.items():
        idx = _RATE_TO_P_INDEX.get(rate)
        if idx is None:
            continue
        _sub(fak, ns, f"P_13_{idx}", _money(net))
        if rate not in (VATRate.ZW, VATRate.NP, VATRate.OO):
            _sub(fak, ns, f"P_14_{idx}", _money(vat))

    _sub(fak, ns, "P_15", _money(invoice.total_gross.amount))

    # P_16..P_23 are boolean flags for special invoice attributes
    # (split-payment, MPP, self-invoicing, etc.). For V1 we emit
    # all-false; settings page (Phase 4 chunk 4) and per-invoice review
    # extensions can flip them later.
    for tag in ("P_16", "P_17", "P_18", "P_18A", "P_19", "P_20",
                "P_21", "P_22", "P_23"):
        _sub(fak, ns, tag, "false")

    _sub(fak, ns, "RodzajFaktury", _INVOICE_TYPE_TO_RODZAJ[invoice.invoice_type])


def _format_address(cp: object) -> str:
    """Single-line address used in P_3B / P_3D."""
    parts: list[str] = []
    for attr in ("address_line1", "address_line2", "postal_code", "city"):
        v = getattr(cp, attr, None)
        if v:
            parts.append(str(v))
    return ", ".join(parts) if parts else "—"


def _build_faktura_ctrl(
    root: etree._Element, ns: str, *, invoice: CanonicalInvoice,
) -> None:
    ctrl = _sub(root, ns, "FakturaCtrl")
    _sub(ctrl, ns, "LiczbaFaktur", "1")
    _sub(ctrl, ns, "WartoscFaktur", _money(invoice.total_gross.amount))


def _build_faktura_wiersze(
    root: etree._Element, ns: str, *, invoice: CanonicalInvoice,
) -> None:
    for line in invoice.lines:
        wiersz = _sub(root, ns, "FakturaWiersz", typ="G")
        _sub(wiersz, ns, "P_2B", invoice.invoice_number)
        _sub(wiersz, ns, "P_7", line.description)
        _sub(wiersz, ns, "P_8A", line.unit or "szt.")
        _sub(wiersz, ns, "P_8B", _q4(line.quantity))
        _sub(wiersz, ns, "P_9A", _money(line.unit_price_net.amount))
        _sub(wiersz, ns, "P_11", _money(line.net_value.amount))
        _sub(wiersz, ns, "P_12", line.vat_rate.value)


def _build_faktura_wiersz_ctrl(
    root: etree._Element, ns: str, *, invoice: CanonicalInvoice,
) -> None:
    ctrl = _sub(root, ns, "FakturaWierszCtrl")
    _sub(ctrl, ns, "LiczbaWierszyFaktur", str(len(invoice.lines)))
    total_net = sum((ln.net_value.amount for ln in invoice.lines), Decimal(0))
    _sub(ctrl, ns, "WartoscWierszyFaktur", _money(total_net))


def to_bytes(
    invoice: CanonicalInvoice,
    *,
    org_name: str,
    org_nip: str | None,
    org_kod_urzedu: str | None,
    cel_zlozenia: int = 1,
) -> bytes:
    """Return the JPK_FA(4) XML as pretty-printed UTF-8 bytes."""
    root = build_jpk_fa(
        invoice,
        org_name=org_name,
        org_nip=org_nip,
        org_kod_urzedu=org_kod_urzedu,
        cel_zlozenia=cel_zlozenia,
    )
    out: bytes = etree.tostring(
        root,
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
        standalone=False,
    )
    return out


def validate_xsd(xml_bytes: bytes) -> list[str]:
    """Optionally validate the XML against the bundled XSD.

    Returns the list of validation errors. Empty list = either valid
    or no XSD present (the file is best-effort). The endpoint logs
    the result but does not fail the download on validation errors —
    operators occasionally produce technically-valid-but-XSD-tight
    JPK files for edge cases, and we'd rather surface a warning than
    refuse the export.
    """
    if not JPK_FA_XSD_PATH.is_file():
        return []
    try:
        schema_doc = etree.parse(str(JPK_FA_XSD_PATH))
        schema = etree.XMLSchema(schema_doc)
    except etree.XMLSchemaParseError as e:
        return [f"XSD load failed: {e}"]

    doc = etree.fromstring(xml_bytes)
    if schema.validate(doc):
        return []
    return [str(err) for err in schema.error_log]

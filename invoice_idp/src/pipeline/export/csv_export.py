"""CSV export — one row per line item, denormalised invoice header.

Layout: each row = one `LineItem` with invoice + seller + buyer columns
repeated. This is what Polish accountants paste into Excel for monthly
reconciliation. Header-only ("one row per invoice") and XLSX
multi-sheet are V1.1 (SPEC §7.2); skipped here.

Output is UTF-8 with BOM so Excel on Windows picks up the encoding
automatically; column names are Polish to match what users see in
their accounting software. Decimal amounts use `.` separator (Polish
Excel handles both, and `.` is unambiguous in CSV).
"""

from __future__ import annotations

import csv
import io

from src.models.invoice import CanonicalInvoice

_COLUMNS: list[str] = [
    "Numer faktury",
    "Typ",
    "Data wystawienia",
    "Data sprzedaży",
    "Waluta",
    "Sprzedawca",
    "NIP sprzedawcy",
    "Nabywca",
    "NIP nabywcy",
    "Lp.",
    "Opis",
    "Ilość",
    "J.m.",
    "Stawka VAT",
    "Wartość netto",
    "Kwota VAT",
    "Wartość brutto",
    "Razem netto (faktura)",
    "Razem VAT (faktura)",
    "Razem brutto (faktura)",
    "Termin płatności",
    "Sposób płatności",
]


def to_bytes(invoice: CanonicalInvoice) -> bytes:
    """Render the invoice as one CSV row per line item (UTF-8 with BOM)."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=",", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(_COLUMNS)

    currency = invoice.total_net.currency.value
    base = [
        invoice.invoice_number,
        invoice.invoice_type.value,
        invoice.issue_date.isoformat(),
        invoice.sale_date.isoformat() if invoice.sale_date else "",
        currency,
        invoice.seller.name,
        invoice.seller.nip or "",
        invoice.buyer.name,
        invoice.buyer.nip or "",
    ]
    totals = [
        f"{invoice.total_net.amount:.2f}",
        f"{invoice.total_vat.amount:.2f}",
        f"{invoice.total_gross.amount:.2f}",
        invoice.payment.due_date.isoformat() if invoice.payment.due_date else "",
        invoice.payment.method or "",
    ]

    if not invoice.lines:
        writer.writerow(base + ["", "", "", "", "", "", "", ""] + totals)
    else:
        for line in invoice.lines:
            writer.writerow(base + [
                line.line_no,
                line.description,
                f"{line.quantity:f}".rstrip("0").rstrip("."),
                line.unit,
                line.vat_rate.value,
                f"{line.net_value.amount:.2f}",
                f"{line.vat_value.amount:.2f}",
                f"{line.gross_value.amount:.2f}",
            ] + totals)

    # Excel-friendly UTF-8 BOM so Polish characters render on Windows.
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")

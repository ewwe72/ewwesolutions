"""Validation checks on CanonicalInvoice — populate extraction_warnings.

Hard warnings indicate data inconsistencies that block export until the
user reviews. Soft warnings are advisory.
"""

from __future__ import annotations

from decimal import Decimal

from src.models.invoice import CanonicalInvoice, InvoiceType, VATRate
from src.utils.nip import is_valid_nip
from src.utils.regon import is_valid_regon

HARD_PENALTY = 0.10
SOFT_PENALTY = 0.03

# Per §5: monetary comparisons tolerate ±0.02 PLN to absorb rounding.
TOLERANCE = Decimal("0.02")

VAT_NUMERIC_RATES: dict[VATRate, Decimal] = {
    VATRate.R23: Decimal("0.23"),
    VATRate.R8: Decimal("0.08"),
    VATRate.R5: Decimal("0.05"),
    VATRate.R0: Decimal("0"),
}


def _within_tolerance(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= TOLERANCE


def validate(invoice: CanonicalInvoice) -> tuple[list[str], list[str]]:
    """Return (hard_warnings, soft_warnings) for the given invoice."""
    hard: list[str] = []
    soft: list[str] = []

    for role in ("seller", "buyer"):
        cp = getattr(invoice, role)
        if cp.nip and not is_valid_nip(cp.nip):
            hard.append(f"{role}.nip checksum invalid: {cp.nip}")
        if cp.regon and not is_valid_regon(cp.regon):
            hard.append(f"{role}.regon checksum invalid: {cp.regon}")

    if invoice.invoice_type in (InvoiceType.REGULAR, InvoiceType.CORRECTION) and not invoice.seller.nip:
        hard.append(f"seller.nip required for invoice_type={invoice.invoice_type.value}")

    currencies = {invoice.total_net.currency, invoice.total_vat.currency, invoice.total_gross.currency}
    for line in invoice.lines:
        currencies.update({
            line.unit_price_net.currency,
            line.net_value.currency,
            line.vat_value.currency,
            line.gross_value.currency,
        })
    if len(currencies) > 1:
        hard.append(f"mixed currencies in invoice: {sorted(c.value for c in currencies)}")

    for line in invoice.lines:
        rate = VAT_NUMERIC_RATES.get(line.vat_rate)
        if rate is not None:
            expected_vat = (line.net_value.amount * rate).quantize(Decimal("0.01"))
            if not _within_tolerance(expected_vat, line.vat_value.amount):
                hard.append(
                    f"line {line.line_no}: VAT math off — "
                    f"{line.net_value.amount} × {rate} = {expected_vat}, got {line.vat_value.amount}"
                )
        if not _within_tolerance(
            line.net_value.amount + line.vat_value.amount,
            line.gross_value.amount,
        ):
            hard.append(
                f"line {line.line_no}: net+vat≠gross — "
                f"{line.net_value.amount}+{line.vat_value.amount}≠{line.gross_value.amount}"
            )

    lines_net = sum((ln.net_value.amount for ln in invoice.lines), Decimal(0))
    lines_vat = sum((ln.vat_value.amount for ln in invoice.lines), Decimal(0))
    lines_gross = sum((ln.gross_value.amount for ln in invoice.lines), Decimal(0))
    if not _within_tolerance(lines_net, invoice.total_net.amount):
        hard.append(f"sum(lines.net)={lines_net} ≠ total_net={invoice.total_net.amount}")
    if not _within_tolerance(lines_vat, invoice.total_vat.amount):
        hard.append(f"sum(lines.vat)={lines_vat} ≠ total_vat={invoice.total_vat.amount}")
    if not _within_tolerance(lines_gross, invoice.total_gross.amount):
        hard.append(f"sum(lines.gross)={lines_gross} ≠ total_gross={invoice.total_gross.amount}")

    if invoice.vat_summary:
        by_rate_lines: dict[VATRate, Decimal] = {}
        for ln in invoice.lines:
            by_rate_lines[ln.vat_rate] = by_rate_lines.get(ln.vat_rate, Decimal(0)) + ln.net_value.amount
        for entry in invoice.vat_summary:
            expected = by_rate_lines.get(entry.rate, Decimal(0))
            if not _within_tolerance(expected, entry.net_total.amount):
                hard.append(
                    f"vat_summary[{entry.rate.value}].net_total={entry.net_total.amount} "
                    f"≠ aggregated lines={expected}"
                )

    if invoice.payment.due_date and invoice.payment.due_date < invoice.issue_date:
        soft.append(f"due_date {invoice.payment.due_date} < issue_date {invoice.issue_date}")

    return hard, soft


def compute_overall_confidence(
    field_confidences: dict[str, float],
    hard_count: int,
    soft_count: int,
) -> float:
    """Per §6: base = mean(field_confidences) − 0.10·hard − 0.03·soft."""
    if not field_confidences:
        base = 0.5
    else:
        base = sum(field_confidences.values()) / len(field_confidences)
    penalty = HARD_PENALTY * hard_count + SOFT_PENALTY * soft_count
    return max(0.0, base - penalty)

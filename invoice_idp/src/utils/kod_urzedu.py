"""Polish KodUrzedu (tax office code) validation.

The Ministry of Finance assigns each urząd skarbowy a 4-digit code (see
podatki.gov.pl → wykaz urzędów skarbowych). JPK_FA filings without a
matching `<KodUrzedu>` are rejected by the PUE portal, so we validate
the shape at settings-save time rather than at export time.

We do not bundle the full list of codes — that file changes on its own
clock — but we do enforce the canonical 4-digit shape so the most
common operator mistakes (typing a NIP, leaving the field blank, or
copy-pasting "0202 — US Warszawa-Mokotów") are caught early.
"""

from __future__ import annotations


def normalize_kod_urzedu(kod: str) -> str:
    """Strip whitespace and non-digit characters."""
    return "".join(c for c in kod if c.isdigit())


def is_valid_kod_urzedu(kod: str) -> bool:
    """4 digits, all numeric. The Ministry's list uses NNNN format."""
    digits = normalize_kod_urzedu(kod)
    return len(digits) == 4 and digits.isdigit()

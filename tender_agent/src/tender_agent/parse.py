"""HTML → TenderAnnouncement parser.

The BZP HTML body is generated from a server-side template — uniform
across announcements. Every field has the shape:

    <h3>X.Y.Z.) Field name: <span class="normal">value</span></h3>
or:
    <h3>X.Y.Z.) Field name</h3>
    <p>value</p>

We extract by the numeric prefix (X.Y.Z.) which is stable across
versions. Where the API metadata already gives us the field (CPV,
organization NIP, dates), we prefer that — pre-parsed = no regex bugs.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from bs4 import BeautifulSoup, Tag

from .models import CpvCode, Criterion, TenderAnnouncement


# `4.2.6.) Główny kod CPV: 72200000-7 - Usługi programowania oprogramowania ...`
_CPV_LINE = re.compile(r"^\s*(\d{8}-\d)\s*[-–]\s*(.+?)\s*$", re.MULTILINE)

# `1.4) Krajowy Numer Identyfikacyjny: <span>REGON 050644804</span>` — extract
# the numeric part. Some authorities use the full 14-digit REGON; treat 9-14
# digit runs after the REGON label as the identifier.
_REGON_INLINE = re.compile(r"REGON\s+(\d{9,14})", re.IGNORECASE)


def _find_field(soup: BeautifulSoup, prefix: str) -> Optional[str]:
    """Find the value following an `h3` that starts with `prefix.`.

    Two layouts are common:
      A) `<h3>1.5.1.) Ulica: <span class="normal">Komandorska 118/120</span></h3>`
         → return the span text.
      B) `<h3>4.2.2.) Krótki opis przedmiotu zamówienia</h3><p>Przedmiotem ...</p>`
         → return the following `<p>` text.

    Returns None if no h3 starts with the prefix or its value can't
    be located. Caller treats None as "field absent in this notice".
    """
    needle = prefix.rstrip(".")  # tolerate `1.5.1.` or `1.5.1`
    for h3 in soup.find_all("h3"):
        text = h3.get_text(strip=True)
        if not text.startswith(needle):
            continue
        # Layout A: span inside h3 with class="normal".
        span = h3.find("span", class_="normal")
        if span:
            value = span.get_text(strip=True)
            if value:
                return value
        # Layout B: next sibling <p>.
        nxt = h3.find_next_sibling()
        while nxt and getattr(nxt, "name", None) == "h3":
            # Skip nested h3s (e.g., 1.5.1 right after 1.5).
            nxt = nxt.find_next_sibling()
        if nxt and getattr(nxt, "name", None) == "p":
            return nxt.get_text(strip=True) or None
        # Layout C: raw text + inline tags between this h3 and the next
        # h2/h3. BeautifulSoup's `find_next_siblings()` filters out
        # NavigableString text nodes; we need `next_siblings` (no `find_`)
        # to walk text + Tags interleaved.
        tail: list[str] = []
        for sibling in h3.next_siblings:
            name = getattr(sibling, "name", None)
            if name in {"h2", "h3"}:
                break
            if isinstance(sibling, Tag):
                t = sibling.get_text(" ", strip=True)
            else:
                t = str(sibling).strip()
            if t:
                tail.append(t)
        joined = " ".join(tail).strip()
        return joined or None
    return None


def _parse_cpv_block(text: Optional[str]) -> Optional[CpvCode]:
    """Pull the first `NNNNNNNN-N - <label>` triple from a CPV string."""
    if not text:
        return None
    m = _CPV_LINE.search(text)
    if not m:
        # Try a looser split.
        parts = text.split(" - ", 1) if " - " in text else text.split("-", 1)
        if len(parts) == 2 and len(parts[0].strip()) >= 7:
            return CpvCode(code=parts[0].strip(), label=parts[1].strip())
        return None
    return CpvCode(code=m.group(1), label=m.group(2).strip())


def _parse_additional_cpvs(soup: BeautifulSoup) -> list[CpvCode]:
    """Walk through the additional-CPV `<p>` block after 4.2.7."""
    for h3 in soup.find_all("h3"):
        if h3.get_text(strip=True).startswith("4.2.7"):
            out: list[CpvCode] = []
            for sibling in h3.find_next_siblings():
                if getattr(sibling, "name", None) in {"h2", "h3"}:
                    break
                if getattr(sibling, "name", None) == "p":
                    cpv = _parse_cpv_block(sibling.get_text(" ", strip=True))
                    if cpv:
                        out.append(cpv)
            return out
    return []


def _extract_regon(soup: BeautifulSoup) -> Optional[str]:
    """Read the `1.4) Krajowy Numer Identyfikacyjny:` span and pull the
    REGON digits if present. Returns None if the field uses something
    other than REGON (rare — some authorities use 14-digit, some have
    only NIP)."""
    field = _find_field(soup, "1.4")
    if not field:
        return None
    m = _REGON_INLINE.search(field)
    return m.group(1) if m else None


def _parse_criteria(soup: BeautifulSoup) -> list[Criterion]:
    """Pull each `Kryterium N` block — name (4.3.5) + waga (4.3.6)."""
    criteria: list[Criterion] = []
    current_name: Optional[str] = None
    for h3 in soup.find_all("h3"):
        text = h3.get_text(strip=True)
        if text.startswith("4.3.5.)"):
            span = h3.find("span", class_="normal")
            if span:
                current_name = span.get_text(strip=True)
        elif text.startswith("4.3.6.)") and current_name:
            span = h3.find("span", class_="normal")
            if span:
                try:
                    weight = float(span.get_text(strip=True).replace(",", "."))
                    criteria.append(Criterion(name=current_name, weight_pct=weight))
                except ValueError:
                    pass
            current_name = None
    return criteria


def parse_record(raw: dict[str, Any], *, source_url: Optional[str] = None) -> TenderAnnouncement:
    """Build a TenderAnnouncement from the raw API + HTML body.

    Prefers structured API fields where available, falls back to HTML
    scraping for anything API doesn't surface.
    """
    html = raw.get("htmlBody") or ""
    soup = BeautifulSoup(html, "lxml") if html else BeautifulSoup("", "lxml")

    # Dates — API gives ISO-8601 with Z; pydantic handles that natively.
    publication_date = datetime.fromisoformat(
        raw["publicationDate"].replace("Z", "+00:00")
    )
    submitting = raw.get("submittingOffersDate")
    submitting_dt = (
        datetime.fromisoformat(submitting.replace("Z", "+00:00")) if submitting else None
    )

    # CPV: API gives a comma-joined string of `NNNNNNNN-N (label)` triples,
    # but the HTML 4.2.6 / 4.2.7 fields parse cleaner.
    cpv_main = _parse_cpv_block(_find_field(soup, "4.2.6"))
    cpv_additional = _parse_additional_cpvs(soup)

    # Fallback to API's `cpvCode` if HTML parse missed.
    if not cpv_main and raw.get("cpvCode"):
        api_cpv_first = raw["cpvCode"].split(",", 1)[0]
        # Format from API: "34996300-8 (Parkingowe ...)"
        if "(" in api_cpv_first:
            code, label = api_cpv_first.split("(", 1)
            cpv_main = CpvCode(code=code.strip(), label=label.rstrip(")").strip())

    return TenderAnnouncement(
        # Identity
        notice_number=raw["noticeNumber"],
        bzp_number=raw["bzpNumber"],
        tender_id_ocds=raw["tenderId"],
        notice_type=raw["noticeType"],
        publication_date=publication_date,
        submitting_offers_date=submitting_dt,

        # Authority (API gives NIP; HTML 1.4 has REGON when present)
        organization_name=raw["organizationName"],
        organization_city=raw["organizationCity"],
        organization_country=raw.get("organizationCountry") or "PL",
        organization_nip=raw["organizationNationalId"],
        organization_regon=_extract_regon(soup),

        # Authority (HTML — addresses, contact)
        organization_address_street=_find_field(soup, "1.5.1"),
        organization_address_postcode=_find_field(soup, "1.5.3"),
        organization_email=_find_field(soup, "1.5.9"),
        organization_website=_find_field(soup, "1.5.10"),
        organization_role_description=_find_field(soup, "1.6"),
        organization_business_description=_find_field(soup, "1.7"),

        # Subject
        order_object=raw["orderObject"],
        order_type=raw["orderType"],
        short_description=_find_field(soup, "4.2.2"),
        cpv_main=cpv_main,
        cpv_additional=cpv_additional,
        realization_period=_find_field(soup, "4.2.10"),

        # Evaluation
        criteria=_parse_criteria(soup),

        # Eligibility
        participation_conditions=_find_field(soup, "5.4"),
        required_evidence=_find_field(soup, "5.7"),

        # Procedure
        procedure_basis=_find_field(soup, "2.16"),
        tender_amount_below_eu=bool(raw.get("isTenderAmountBelowEU")),
        procurement_platform_url=_find_field(soup, "3.1"),

        # Provenance
        raw_html_size_bytes=len(html) if html else None,
        raw_api_url=source_url,
    )

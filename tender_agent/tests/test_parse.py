"""Parser tests — synthetic fixture + opportunistic real cached samples.

The synthetic fixture (`tests/fixtures/minimal_announcement.html`) is the
contract: it exercises every parser path and runs without any
network/cache state. The real-sample tests are integration-flavoured
— they run if `_samples/<id>/raw.json` is cached locally (operator's
working tree) and skip cleanly otherwise.

No LLM calls; no httpx traffic; pytest-only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tender_agent.parse import parse_record


FIXTURES = Path(__file__).parent / "fixtures"
SAMPLES_ROOT = Path(__file__).parent.parent / "_samples"


# ---------- synthetic fixture: covers every parser path ----------


def _synthetic_raw() -> dict[str, object]:
    """Build the API-side metadata shape that wraps the minimal HTML body."""
    return {
        "noticeNumber": "2026/BZP 00099999/01",
        "bzpNumber": "2026/BZP 00099999",
        "tenderId": "ocds-148610-deadbeef-0000-0000-0000-000000000001",
        "noticeType": "ContractNotice",
        "publicationDate": "2026-05-12T08:00:00.0000000Z",
        "submittingOffersDate": "2026-05-26T10:00:00Z",
        "organizationName": "URZĄD GMINY EXAMPLEOWO",
        "organizationCity": "Exampleowo",
        "organizationCountry": "PL",
        "organizationNationalId": "5252999888",
        "orderObject": "Dostawa i wdrożenie systemu DMS dla urzędu gminy",
        "orderType": "Services",
        "isTenderAmountBelowEU": True,
        "cpvCode": "48000000-8 (Pakiety oprogramowania i systemy informatyczne)",
        "htmlBody": (FIXTURES / "minimal_announcement.html").read_text(encoding="utf-8"),
    }


def test_parse_identity_fields() -> None:
    ann = parse_record(_synthetic_raw())
    assert ann.bzp_number == "2026/BZP 00099999"
    assert ann.notice_number == "2026/BZP 00099999/01"
    assert ann.tender_id_ocds.startswith("ocds-148610-")
    assert ann.notice_type == "ContractNotice"
    assert ann.publication_date.year == 2026
    assert ann.submitting_offers_date is not None


def test_parse_separates_nip_and_regon() -> None:
    """The session-discovered bug: API gives NIP, HTML has REGON. Both must surface."""
    ann = parse_record(_synthetic_raw())
    assert ann.organization_nip == "5252999888"
    assert ann.organization_regon == "000123456"


def test_parse_authority_contact() -> None:
    ann = parse_record(_synthetic_raw())
    assert ann.organization_address_street == "Główna 1"
    assert ann.organization_address_postcode == "00-001"
    assert ann.organization_email == "zamowienia@example.gov.pl"
    assert ann.organization_website == "https://example.gov.pl"
    assert ann.organization_role_description
    assert "samorządu terytorialnego" in ann.organization_role_description
    assert ann.organization_business_description == "Ogólne usługi publiczne"


def test_parse_main_cpv_from_html_not_api() -> None:
    """HTML's 4.2.6 line gives cleaner code+label than the API's joined string."""
    ann = parse_record(_synthetic_raw())
    assert ann.cpv_main is not None
    assert ann.cpv_main.code == "48000000-8"
    assert "Pakiety oprogramowania" in ann.cpv_main.label


def test_parse_additional_cpvs_in_order() -> None:
    ann = parse_record(_synthetic_raw())
    codes = [c.code for c in ann.cpv_additional]
    assert codes == ["72263000-6", "72253200-5"]


def test_parse_criteria_pairs_name_with_weight() -> None:
    ann = parse_record(_synthetic_raw())
    criteria = {c.name: c.weight_pct for c in ann.criteria}
    assert criteria == {"Cena": 60.0, "Gwarancja": 40.0}


def test_parse_participation_and_evidence() -> None:
    ann = parse_record(_synthetic_raw())
    assert ann.participation_conditions
    assert "ostatnich 3 lat" in ann.participation_conditions
    assert ann.required_evidence
    assert "Wykaz dostaw" in ann.required_evidence


def test_parse_procedure_basis_uses_paragraph_sibling() -> None:
    """2.16 uses Layout B (p sibling), not the span-in-h3 layout."""
    ann = parse_record(_synthetic_raw())
    assert ann.procedure_basis
    assert "art. 275" in ann.procedure_basis


def test_parse_procurement_platform_uses_text_tail() -> None:
    """3.1 uses Layout C (raw text after h3) — exercises that fallback path."""
    ann = parse_record(_synthetic_raw())
    assert ann.procurement_platform_url
    assert "example.gov.pl/zamowienia" in ann.procurement_platform_url


def test_parse_threshold_flag() -> None:
    ann = parse_record(_synthetic_raw())
    assert ann.tender_amount_below_eu is True


def test_parse_realization_period() -> None:
    ann = parse_record(_synthetic_raw())
    assert ann.realization_period == "90 dni"


def test_parse_optional_fields_handle_missing() -> None:
    """Strip the participation/evidence sections and confirm None, not crash."""
    raw = _synthetic_raw()
    html = raw["htmlBody"]
    assert isinstance(html, str)
    # Cut out the entire SEKCJA V block.
    cut = html.replace("<h3 class=\"mb-0\">5.4.)", "<!-- removed -->")
    cut = cut.replace("<h3 class=\"mb-0\">5.7.)", "<!-- removed -->")
    raw["htmlBody"] = cut
    ann = parse_record(raw)
    assert ann.participation_conditions is None
    assert ann.required_evidence is None


# ---------- opportunistic real-sample integration tests ----------


REAL_CACHE_IDS = [
    "2026-BZP-00236579",  # Łapy hospital — domain authentication, CPV 72263000
    "2026-BZP-00237383",  # Powiat Pucki — GIS db modernisation, CPV 72320000
    "2026-BZP-00236925",  # Szpital Miastko — maintenance contract, CPV 72253200
]


@pytest.mark.parametrize("sample_id", REAL_CACHE_IDS)
def test_parses_cached_real_announcement(sample_id: str) -> None:
    raw_path = SAMPLES_ROOT / sample_id / "raw.json"
    if not raw_path.exists():
        pytest.skip(f"Real sample not cached locally: {sample_id}")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    ann = parse_record(raw)
    # Sanity invariants every announcement satisfies:
    assert ann.bzp_number == sample_id.replace("-", "/").replace("BZP/", "BZP ", 1).replace("/", " ", 0)  # cosmetic re-format
    # The hard checks — these are real data, can't fake:
    assert ann.organization_name
    assert ann.organization_nip and ann.organization_nip.isdigit()
    assert ann.cpv_main is not None
    assert ann.cpv_main.code.startswith("72")  # all 3 are CPV 72*
    assert ann.criteria, "every contract notice has at least one criterion"


@pytest.mark.parametrize("sample_id", REAL_CACHE_IDS)
def test_real_sample_has_kancelaryjny_polish(sample_id: str) -> None:
    """Smoke check: the HTML body actually contains Polish diacritics in
    expected fields. Catches mis-encoded fetches."""
    raw_path = SAMPLES_ROOT / sample_id / "raw.json"
    if not raw_path.exists():
        pytest.skip(f"Real sample not cached locally: {sample_id}")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    html = raw.get("htmlBody", "")
    assert any(ch in html for ch in "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"), (
        "HTML body has no Polish diacritics — fetched as latin1?"
    )

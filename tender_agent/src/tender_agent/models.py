"""Pydantic models for BZP announcements + firm profile + drafts.

The shape mirrors what the BZP API returns (mo-board) plus what the
drafter prompt needs as input. Keep it minimal — Phase 0 prototype,
not the full SIWZ + JEDZ + załączniki universe yet.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CpvCode(BaseModel):
    """One CPV (Common Procurement Vocabulary) code with its Polish label."""
    code: str = Field(description="Dotted code e.g. `72200000-7`.")
    label: str = Field(description="Human-readable Polish description.")


class Criterion(BaseModel):
    """One bid-evaluation criterion (cena, gwarancja, doświadczenie, etc.)."""
    name: str
    weight_pct: float = Field(description="0-100, % weight in evaluation.")


class TenderAnnouncement(BaseModel):
    """Structured view of a BZP ogłoszenie o zamówieniu (ContractNotice).

    Fields are extracted from the API response + the embedded HTML body.
    Optional fields are left None when the source announcement omits them.
    """
    # Identity
    notice_number: str = Field(description="e.g. `2026/BZP 00240644/01`")
    bzp_number: str = Field(description="e.g. `2026/BZP 00240644` (no version suffix)")
    tender_id_ocds: str = Field(description="OCDS-format UUID, e.g. `ocds-148610-...`")
    notice_type: str
    publication_date: datetime
    submitting_offers_date: Optional[datetime] = None

    # Contracting authority (Zamawiający)
    organization_name: str
    organization_city: str
    organization_country: str = "PL"
    organization_nip: str = Field(
        description="Numer Identyfikacji Podatkowej (10 cyfr). API `organizationNationalId` field."
    )
    organization_regon: Optional[str] = Field(
        default=None,
        description="REGON (9 lub 14 cyfr), extracted from HTML pkt 1.4.) Krajowy Numer Identyfikacyjny. Often absent on small contracting authorities.",
    )
    organization_address_street: Optional[str] = None
    organization_address_postcode: Optional[str] = None
    organization_email: Optional[str] = None
    organization_website: Optional[str] = None
    organization_role_description: Optional[str] = None
    organization_business_description: Optional[str] = None

    # Subject
    order_object: str = Field(description="Krótki opis (one-line title).")
    order_type: str = Field(description="`Delivery` | `Services` | `Works` | `Mixed`.")
    short_description: Optional[str] = Field(
        default=None, description="Pkt 4.2.2.) Krótki opis przedmiotu zamówienia."
    )
    cpv_main: Optional[CpvCode] = None
    cpv_additional: list[CpvCode] = Field(default_factory=list)
    realization_period: Optional[str] = Field(
        default=None, description="e.g. `42 dni`."
    )

    # Criteria
    criteria: list[Criterion] = Field(default_factory=list)

    # Conditions (warunki udziału w postępowaniu)
    participation_conditions: Optional[str] = Field(
        default=None,
        description="Pkt 5.4.) Nazwa i opis warunków udziału w postępowaniu — full text.",
    )
    required_evidence: Optional[str] = Field(
        default=None,
        description="Pkt 5.7.) Wykaz podmiotowych środków dowodowych — full text.",
    )

    # Procedure
    procedure_basis: Optional[str] = Field(
        default=None,
        description="Pkt 2.16.) Tryb udzielenia zamówienia wraz z podstawą prawną.",
    )
    tender_amount_below_eu: bool = Field(
        description="True = krajowy (poniżej progu UE), False = unijny."
    )

    # Bidding logistics
    procurement_platform_url: Optional[str] = Field(
        default=None,
        description="Pkt 3.1.) Adres strony internetowej prowadzonego postępowania.",
    )

    # Raw, for debugging / fail-case analysis
    raw_html_size_bytes: Optional[int] = None
    raw_api_url: Optional[str] = None


class FirmProfile(BaseModel):
    """Minimal firm profile for Phase 0 drafting.

    In Phase 1 this expands to certifications, prior wins, key personnel
    CVs, CPV preferences, etc. (see specs.md §3 Ingest).
    """
    legal_name: str
    short_name: str
    nip: str
    regon: Optional[str] = None
    krs: Optional[str] = None
    address: str
    representative: str = Field(
        description="Person signing the bid, e.g. 'Jan Kowalski, Prezes Zarządu'."
    )
    contact_email: str
    contact_phone: Optional[str] = None


class SiwzWarunek(BaseModel):
    """One condition for participation in the procedure (warunek udziału)."""
    text: str = Field(
        description=(
            "Pełna treść warunku w jezyku oryginału, "
            "np. 'wykonawca musi wykazać co najmniej 2 zrealizowane usługi w okresie "
            "ostatnich 3 lat o wartości min. 50 000 PLN każda'."
        )
    )
    category: str = Field(
        description=(
            "Jedna z: 'zdolność techniczna', 'sytuacja ekonomiczna', "
            "'kompetencje', 'uprawnienia', 'inna'."
        )
    )
    evidence_required: Optional[str] = Field(
        default=None,
        description=(
            "Dokument lub środek dowodowy, którym wykonawca potwierdza spełnienie "
            "warunku, np. 'wykaz usług + dowody należytego wykonania'. "
            "None jeśli SIWZ nie sprecyzowało."
        ),
    )


class SiwzKryterium(BaseModel):
    """One bid-evaluation criterion from the SIWZ (kryterium oceny)."""
    name: str = Field(description="Nazwa kryterium, np. 'cena', 'okres gwarancji'.")
    weight_percent: float = Field(description="Waga procentowa, 0-100.")
    description: Optional[str] = Field(
        default=None,
        description="Sposób oceny / formuła punktacji, jeżeli SIWZ ją podaje.",
    )


class SiwzKaraUmowna(BaseModel):
    """One contract penalty clause (kara umowna)."""
    trigger: str = Field(
        description="Zdarzenie wywołujące karę, np. 'zwłoka w realizacji usługi'."
    )
    amount: str = Field(
        description=(
            "Wysokość kary jako tekst, np. '0,2% wynagrodzenia za każdy dzień zwłoki' "
            "albo '10% wartości umowy'."
        )
    )


class SiwzTerminy(BaseModel):
    """Deadlines + periods from the SIWZ.

    Strings (not datetimes) because SIWZ texts are inconsistent — some
    give date+time, some only date, some 'X dni od podpisania umowy'.
    Drafter receives the source text 1:1.
    """
    skladanie_ofert: Optional[str] = Field(
        default=None,
        description="Termin składania ofert (data + godzina jeśli podane).",
    )
    otwarcie_ofert: Optional[str] = Field(
        default=None, description="Termin otwarcia ofert."
    )
    zwiazania_oferta: Optional[str] = Field(
        default=None,
        description="Okres związania ofertą, np. '30 dni od terminu składania ofert'.",
    )
    realizacja: Optional[str] = Field(
        default=None,
        description="Okres realizacji zamówienia, np. '6 miesięcy od podpisania umowy'.",
    )


class SiwzWadium(BaseModel):
    """Wadium (bid bond) requirement, when the SIWZ demands one."""
    amount: str = Field(description="Wysokość wadium, np. '5 000 PLN'.")
    form: Optional[str] = Field(
        default=None,
        description=(
            "Dopuszczalne formy wniesienia wadium, np. "
            "'pieniądz / gwarancja bankowa / gwarancja ubezpieczeniowa'."
        ),
    )


class SiwzKontakt(BaseModel):
    """Procurement officer contact (osoba prowadząca postępowanie)."""
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class SiwzRequirements(BaseModel):
    """Structured view of an SIWZ (Specyfikacja Warunków Zamówienia).

    Populated by `tender_agent.siwz_extract.extract_requirements`. Feeds
    the drafter so Section D can cite concrete requirements + evidence
    instead of leaving prose 'specialist must verify' notes.

    Hard rule on the extractor: every field is grounded in the source
    PDF. Fields not present in the SIWZ are left None / empty list —
    no fabrication. Drafter relies on `None` as a real signal.
    """

    warunki_udzialu: list[SiwzWarunek] = Field(
        default_factory=list,
        description="Warunki udziału w postępowaniu (pkt 5 SIWZ zwykle).",
    )
    kryteria_oceny: list[SiwzKryterium] = Field(
        default_factory=list,
        description="Kryteria oceny ofert (cena + pozostałe, każde z wagą).",
    )
    wymagane_dokumenty: list[str] = Field(
        default_factory=list,
        description=(
            "Wymagane załączniki do oferty (poza JEDZ), np. wykaz usług, "
            "polisa OC, odpis z KRS. Krótki opis każdego dokumentu."
        ),
    )
    terminy: SiwzTerminy = Field(default_factory=lambda: SiwzTerminy())
    kary_umowne: list[SiwzKaraUmowna] = Field(default_factory=list)
    kontakt: Optional[SiwzKontakt] = None
    jedz_scope: list[str] = Field(
        default_factory=list,
        description=(
            "Które części JEDZ SIWZ wymaga, np. ['I', 'III']. Pusta lista "
            "jeżeli SIWZ nie sprecyzowało (interpretacja należy do specjalisty)."
        ),
    )
    wadium: Optional[SiwzWadium] = None
    additional_notes: list[str] = Field(
        default_factory=list,
        description=(
            "Inne istotne informacje, których nie da się dopasować do pól "
            "powyżej, a o których drafter powinien wiedzieć "
            "(np. szczególne wymagania techniczne, klauzule RODO, "
            "obligatoryjne wizje lokalne)."
        ),
    )

    # Provenance — required, not extracted from PDF content
    source_pdf_path: str = Field(description="Absolute path of the SIWZ PDF.")
    extraction_model: str = Field(
        description="Anthropic model id used, e.g. 'claude-haiku-4-5'."
    )
    pages_extracted: int = Field(description="Number of PDF pages read by pymupdf.")


class DraftBundle(BaseModel):
    """What the drafter produces for one announcement.

    Markdown chunks, one per document. Caller renders / concatenates.
    """
    oswiadczenie_wykluczenie_md: str = Field(
        description="Oświadczenie o niepodleganiu wykluczeniu (boilerplate, low-stakes)."
    )
    jedz_czesc_1_md: str = Field(
        description="JEDZ Część I — informacje o postępowaniu (auto from announcement)."
    )
    list_intencyjny_md: str = Field(
        description="2-3 akapity listu intencyjnego / opening of formal bid letter."
    )

    # Self-reported confidence + caveats from the model
    model_notes: Optional[str] = Field(
        default=None,
        description="Free-form notes from the drafter: gaps, assumptions, things to verify.",
    )

    # JEDZ Parts II-IV (Phase 0.7). Optional so legacy callers / older
    # serialized bundles still validate; drafter populates them on every
    # new run, but a Phase 0.5 bundle re-loaded from disk lacks them.
    jedz_czesc_2_md: Optional[str] = Field(
        default=None,
        description="JEDZ Część II — informacje o wykonawcy (auto from FirmProfile).",
    )
    jedz_czesc_3_md: Optional[str] = Field(
        default=None,
        description="JEDZ Część III — powody wykluczenia (standardowe oświadczenia negatywne).",
    )
    jedz_czesc_4_md: Optional[str] = Field(
        default=None,
        description=(
            "JEDZ Część IV — kryteria kwalifikacji. Forma uproszczona α "
            "gdy SIWZ.warunki_udzialu pusta, szczegółowa gdy SIWZ ma warunki."
        ),
    )


class Finding(BaseModel):
    """One issue found by the verifier sub-agent.

    `excerpt` is verbatim text from the draft (preferred) or a one-line
    description when the issue is structural (e.g. a missing identifier).
    `suggestion` is a short, actionable fix the specialist can apply.
    """
    severity: str = Field(description="`error` | `warn` | `info`.")
    category: str = Field(
        description=(
            "`firm_name_typo` | `wrong_identifier` | `fabricated_siwz_citation` | "
            "`wrong_value` | `inconsistency` | `missing_field` | `other`."
        )
    )
    excerpt: str = Field(
        description="Verbatim text from the draft, or a short structural description."
    )
    suggestion: str = Field(
        description="Concrete fix the specialist can apply (one line)."
    )
    source: str = Field(
        description="`deterministic` (regex/string check) | `llm` (Haiku cross-check).",
    )


class VerificationReport(BaseModel):
    """Result of running the verifier sub-agent on one DraftBundle.

    `passed` is `True` only when **no** `error`-severity findings exist.
    `warn`/`info` don't fail the report — they're surfaced for the
    specialist's review.

    `llm_model` is `None` when the LLM cross-check was skipped (e.g. SIWZ
    absent + no semantic ground truth → deterministic-only pass).
    """
    findings: list[Finding] = Field(default_factory=list)
    passed: bool = Field(description="True iff no `error`-severity findings.")

    # Provenance — useful when re-running with a different model
    draft_path: str = Field(description="Absolute path of the draft.md verified.")
    bzp_number: str = Field(description="Announcement BZP number, e.g. `2026/BZP 00236579`.")
    llm_model: Optional[str] = Field(
        default=None,
        description="Anthropic model id used for the LLM check, or None if skipped.",
    )
    llm_cost_usd: float = Field(
        default=0.0,
        description="Total USD cost of LLM calls during verification.",
    )

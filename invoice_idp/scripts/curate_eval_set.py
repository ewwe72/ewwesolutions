"""Curate eval_set/ — split pulled PDFs into invoices vs noise.

Two-stage classifier:
  1. Filename patterns — strong noise hits (regulamin, manual, MiFID, ...)
     beat strong invoice hits (faktura, FV*, FA*, invoice, paragon, ...).
  2. Ambiguous filenames: open first page, count invoice markers
     (NIP, Faktura, Sprzedawca/Nabywca, Razem, netto/brutto, VAT, PLN, %).

Noise PDFs are moved to eval_set/_noise/ (not deleted). Invoices stay in
eval_set/. Prints a per-reason summary at the end.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

try:
    import pymupdf  # PyMuPDF >= 1.24 renamed module
except ImportError:
    try:
        import fitz as pymupdf  # older alias
    except ImportError:
        print("ERROR: pip install pymupdf", file=sys.stderr)
        sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "eval_set"
NOISE_DIR = EVAL_DIR / "_noise"

NOISE_PATTERNS = re.compile(
    r"(?:"
    r"regulamin|warunki|polityk|prywatnos|instrukcj|manual|"
    r"podr_?cznik|mifid|taryfa|tabela_op|klauzula|wzor[_-]|formularz|"
    r"odstapien|odst_pien|eula|za_?cznik|terms[_-]|withdrawal|cennik|"
    r"wykaz_zmian|obowi_?zki|ekonto-osobiste|twojedokumenty|umowa_|"
    r"bilet_|etykieta|e-recepta|zalecenia|za_?wiadczenie|"
    r"oswiadczenie|karta_produktu|arkusz_informacyjny|"
    r"specyfikacja-domeny|registration-agreement|broszura|"
    r"deposit_insurance|consumer_right|ex_ante|kid[_.]|kid_pl|"
    r"obchodni-podminky|pakiet-|"
    r"komunikat|dane_wlasciciela|reklamacj|gwarancj|_zwrot|"
    r"potwierdzenie_wykonania|potwierdzenie_operacji|"
    r"_potwierdzenie\.|^potwierdzenie\.|"
    r"^notifications_|buyer_advice|"
    r"zestawienie_operacji|wyciag|"
    r"projekt_umowy|zapytanie_ofertowe|umowa_partnera|umowa_zlecenie|"
    r"informacje_dodatkowe|charakterystyka|dobieranie_|"
    r"okre_lenie|organizowanie_|rozr_nianie|system_ochrony|"
    r"fees_pages|joint_accounts|personal_terms|paid_plans|"
    r"mcc_exclusions|addendum|return[_-]label|returnlabel|"
    r"og_lne_warunki|swiadczenia_uslug|"
    r"agreement[_.]|certyfikat_ssl|nazw_domen|hostingu|"
    r"warranty|trading_terms|emakler|maklerski|"
    r"pit-28|p44_p4|popenda_2025"
    r")",
    re.IGNORECASE,
)

INVOICE_PATTERNS = re.compile(
    r"(?:"
    r"faktura|e-faktura|efaktura|"
    r"(?:^|[_-])f[avs][_-]?\d|"      # FA1234, FV-123, FS_123
    r"(?:^|[_-])inv[_-]|"            # INV_SM25..., inv_8025...
    r"invoice|paragon|rachunek|"
    r"korekt|proforma|"
    r"plinv\d|"                      # IKEA PLINV...
    r"_fv[_.]|fv-\d|"
    r"dokument_sprzeda|dokument_zakupu|"
    r"d20017509|"                    # alsachim invoice id pattern
    r"sm\d{2}-22|"                   # allegro SMxx invoice ids
    r"^fb_|_fb_"
    r")",
    re.IGNORECASE,
)

CONTENT_INVOICE_MARKERS = [
    re.compile(r"\bNIP\b"),
    re.compile(r"faktur", re.IGNORECASE),
    re.compile(r"sprzedawca", re.IGNORECASE),
    re.compile(r"nabywca", re.IGNORECASE),
    re.compile(r"\brazem\b", re.IGNORECASE),
    re.compile(r"data\s+wystawi", re.IGNORECASE),
    re.compile(r"data\s+sprzeda", re.IGNORECASE),
    re.compile(r"\bnetto\b", re.IGNORECASE),
    re.compile(r"\bbrutto\b", re.IGNORECASE),
    re.compile(r"\bVAT\b"),
    re.compile(r"\bPLN\b"),
    re.compile(r"\b(?:23|8|5|0)\s?%"),
    re.compile(r"\binvoice\b", re.IGNORECASE),
    re.compile(r"\bseller\b", re.IGNORECASE),
    re.compile(r"\bbuyer\b", re.IGNORECASE),
    re.compile(r"\btotal\b", re.IGNORECASE),
    re.compile(r"\bsubtotal\b", re.IGNORECASE),
    re.compile(r"paragon", re.IGNORECASE),
    re.compile(r"rachunek", re.IGNORECASE),
]

CONTENT_NOISE_MARKERS = [
    re.compile(r"regulamin", re.IGNORECASE),
    re.compile(r"\bpolityka\s+prywatno", re.IGNORECASE),
    re.compile(r"\s§\s*\d"),
    re.compile(r"og[óo]lne\s+warunki", re.IGNORECASE),
    re.compile(r"klauzula\s+informacyjna", re.IGNORECASE),
    re.compile(r"wykaz\s+zmian", re.IGNORECASE),
]


def read_first_page_text(pdf_path: Path, max_chars: int = 6000) -> str | None:
    try:
        with pymupdf.open(pdf_path) as doc:
            if doc.page_count == 0:
                return ""
            text = doc[0].get_text("text") or ""
            return text[:max_chars]
    except Exception:
        return None


def classify(pdf_path: Path) -> tuple[str, str]:
    name = pdf_path.name.lower()

    if NOISE_PATTERNS.search(name):
        return ("noise", "filename:noise")
    if INVOICE_PATTERNS.search(name):
        return ("invoice", "filename:invoice")

    text = read_first_page_text(pdf_path)
    if text is None:
        return ("noise", "pdf:read-failed")

    inv_hits = sum(1 for p in CONTENT_INVOICE_MARKERS if p.search(text))
    noise_hits = sum(1 for p in CONTENT_NOISE_MARKERS if p.search(text))

    if noise_hits >= 2:
        return ("noise", "content:legal-doc")
    if inv_hits >= 4:
        return ("invoice", f"content:strong-{inv_hits}")
    if inv_hits >= 2 and noise_hits == 0:
        return ("invoice", f"content:weak-{inv_hits}")
    return ("noise", f"content:no-signal-{inv_hits}i{noise_hits}n")


def main() -> int:
    if not EVAL_DIR.exists():
        print(f"ERROR: {EVAL_DIR} does not exist", file=sys.stderr)
        return 1

    NOISE_DIR.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(
        p for p in EVAL_DIR.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf"
    )
    print(f"Classifying {len(pdfs)} PDFs...\n")

    invoices: list[tuple[Path, str]] = []
    noise: list[tuple[Path, str]] = []

    for i, pdf in enumerate(pdfs, 1):
        verdict, reason = classify(pdf)
        (invoices if verdict == "invoice" else noise).append((pdf, reason))
        if i % 100 == 0 or i == len(pdfs):
            print(f"  {i}/{len(pdfs)}")

    print(f"\n{len(invoices)} invoices, {len(noise)} noise")

    moved = 0
    for path, _ in noise:
        target = NOISE_DIR / path.name
        if target.exists():
            target.unlink()
        path.rename(target)
        moved += 1

    print(f"Moved {moved} -> {NOISE_DIR.relative_to(ROOT)}/")
    print(f"Remaining in {EVAL_DIR.relative_to(ROOT)}/: {len(invoices)}\n")

    inv_reasons = Counter(r for _, r in invoices)
    noise_reasons = Counter(r for _, r in noise)

    print("Invoice reasons:")
    for r, c in inv_reasons.most_common():
        print(f"  {c:>4}  {r}")
    print("\nNoise reasons:")
    for r, c in noise_reasons.most_common():
        print(f"  {c:>4}  {r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

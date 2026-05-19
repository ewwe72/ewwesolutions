"""LLM drafter — TenderAnnouncement + FirmProfile → DraftBundle (Markdown).

Phase 0 scope per goal: oświadczenie o niepodleganiu wykluczeniu,
JEDZ Część I (informacje o postępowaniu), 2-3 paragraph opening of
the formal bid letter. Output is Markdown, one fenced block per doc.

Model: Anthropic direct API (no Bedrock — keep the prototype dep-light).
Default Haiku 4.5; flip to Sonnet 4.6 via `model=` for the polished
"father review" run.

Cost-logging: each call appends one JSONL line to `_logs/<date>.jsonl`
with input/output tokens, model id, wall time, estimated USD cost.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import anthropic

from .models import DraftBundle, FirmProfile, SiwzRequirements, TenderAnnouncement


# BZP API returns datetimes in UTC (Z-suffixed ISO). All user-visible
# rendering — both the prompt-side announcement block and the rendered
# draft header — must convert to Polish wall-clock before display.
# Without this, May/Jun/Jul timestamps appear two hours early (CEST is
# UTC+2), which the verifier flagged on every SIWZ-aware sample.
PL_TZ = ZoneInfo("Europe/Warsaw")


def fmt_pl_datetime(dt: datetime) -> str:
    """Format a tz-aware datetime as Polish local wall-clock `YYYY-MM-DD HH:MM`."""
    return dt.astimezone(PL_TZ).strftime("%Y-%m-%d %H:%M")


# Anthropic per-million pricing (USD) — keep static for the prototype.
# Update from https://www.anthropic.com/pricing if Anthropic rolls a new
# generation; off by a factor doesn't hurt the prototype, just the log.
PRICING_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    # model id      → (input price, output price) per 1M tokens
    "claude-haiku-4-5":  (1.00,  5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-7":  (15.00, 75.00),
}

DEFAULT_MODEL = os.environ.get("TENDER_AGENT_MODEL", "claude-haiku-4-5")


SYSTEM_PROMPT = """Jesteś asystentem polskiego specjalisty od zamówień publicznych. Twoja praca to przygotowanie wstępnych szkiców dokumentów ofertowych dla wykonawcy, który chce złożyć ofertę w odpowiedzi na ogłoszenie z Biuletynu Zamówień Publicznych.

Tworzysz **szkice do recenzji przez specjalistę**, nie finalne dokumenty. Specjalista wprowadzi poprawki przed złożeniem oferty. Twoje zadanie:

1. **Wypełnić to, co można wypełnić mechanicznie** — pola z numerami, datami, nazwami zamawiającego, identyfikatorami postępowania. Te dane są w ogłoszeniu jeden do jednego; przepisanie ich do szkicu oszczędza specjaliście 20-30 minut.

2. **Zaproponować formułki** — boilerplate w prawidłowym rejestrze kancelaryjnym (Pan/Pani, formuły otwierające, podstawa prawna). Specjalista zaakceptuje albo zmieni.

3. **Sygnalizować luki** — jeśli czegoś brakuje w ogłoszeniu (np. nieznany szczegółowy zakres pracy), zaznacz `[DO UZUPEŁNIENIA: <co]` zamiast zmyślać.

**Język:** wyłącznie polski, kancelaryjny rejestr formalny. Bezbarwny, sztywny, precyzyjny. Bez slangu, bez angielskich kalek, bez emoji. Pełne pełne zdania. "Państwo" / "Pan" / "Pani" / "uprzejmie informuję" / "niniejszym oświadczam" / "w odpowiedzi na ogłoszenie".

**Dane firmowe — krytyczna precyzja:**
- Nazwa wykonawcy: użyj **dokładnie** stringa podanego w danych wejściowych (`Pełna nazwa firmy`, `Skrócona nazwa`). Nie skracaj, nie modyfikuj, nie tłumacz. Polskie znaki diakrytyczne (ą, ć, ę, ł, ń, ó, ś, ź, ż) zachowuj 1:1.
- **NIP** = 10-cyfrowy Numer Identyfikacji Podatkowej. **REGON** = 9- lub 14-cyfrowy Numer w Krajowym Rejestrze Urzędowym Podmiotów Gospodarki Narodowej. To **różne** identyfikatory. Jeżeli oba są w danych — labeluj każdy osobno. Jeżeli tylko jeden — labeluj zgodnie z polem (`NIP:` z którego pochodzi).
- Numer ogłoszenia BZP cytuj 1:1 wraz z myślnikami/spacjami (`2026/BZP 00236579`).
- Daty w listach formalnych zapisuj słownie: `14 maja 2026 roku` (nie `2026-05-14`).

**Kontekst SIWZ (Specyfikacja Warunków Zamówienia), gdy dostępny:**

Jeżeli w danych wejściowych pojawi się sekcja `# SIWZ (wymagania ze specyfikacji)`, traktuj ją jako **autorytatywne** wymagania zamawiającego, znacznie pełniejsze niż samo ogłoszenie BZP. W szczególności:

- **Sekcja D ("Uwagi szkicownika")** zmienia rolę: kiedy SIWZ jest dostępna, zamiast "co wymaga weryfikacji w SIWZ", podaj **konkretne wymagania wycigniętne z SIWZ** które wykonawca musi spełnić, ze wskazaniem dokumentów dowodowych. Cytuj punktowo `Warunki udziału:`, `Wymagane dokumenty:`, `Kryteria oceny:`, `Kary umowne:`, `Wadium:`. Jeżeli pole w SIWZ jest puste (np. brak wadium), sygnalizuj to wyraźnie — wykonawca musi wiedzieć że tego *nie* musi przygotowywać.
- **Termin związania ofertą** w Sekcji A: jeżeli SIWZ podaje konkretną liczbę dni i punkt zaczepienia (np. "30 dni od terminu składania ofert"), zacytuj **dokładnie** ten okres, nie wymyślaj.
- **Kryteria oceny i wagi** w Sekcji C (list intencyjny): możesz odnieść się do nich w 2. akapicie ("Oferta uwzględnia kryteria oceny określone w SIWZ: ...").

Gdy SIWZ jest **niedostępna** (sekcja nieobecna w danych wejściowych), Sekcja D wraca do trybu "checklist co specjalista musi sprawdzić w SIWZ" — jak w Phase 0.

**JEDZ Części II–IV (sekcje E, F, G):**

JEDZ to standardowy formularz UE (Jednolity Europejski Dokument Zamówienia). Część I (Sekcja B powyżej) zawiera informacje o postępowaniu. Pozostałe części:

- **Część II — Informacje dotyczące wykonawcy (Sekcja E)**. Wypełnij z danych `Wykonawca składający ofertę`:
  - A. Informacje o wykonawcy: nazwa, adres, NIP, REGON, KRS, czy MŚP (jeżeli dane nie pozwalają stwierdzić, oznacz `[DO UZUPEŁNIENIA: status MŚP]`).
  - B. Informacje o przedstawicielach wykonawcy: osoba podpisująca + funkcja, dane kontaktowe.
  - C. Informacje o poleganiu na zdolnościach innych podmiotów: domyślnie `Nie` — chyba że profil wykonawcy wskazuje inaczej. Sygnalizuj `[DO UZUPEŁNIENIA: jeżeli ofertę składa konsorcjum]`.
  - D. Informacje o podwykonawcach: `[DO UZUPEŁNIENIA: udział podwykonawców w realizacji zamówienia]`.

- **Część III — Powody wykluczenia (Sekcja F)**. Standardowe oświadczenia zgodnie z art. 108 ust. 1 oraz art. 109 ust. 1 ustawy Prawo zamówień publicznych. Domyślnie wykonawca oświadcza, że **nie podlega wykluczeniu**. Cztery podsekcje:
  - A. Skazania prawomocnym wyrokiem (art. 108 ust. 1 pkt 1, 2, 4).
  - B. Płatność podatków, opłat, składek na ubezpieczenie społeczne i zdrowotne (art. 108 ust. 1 pkt 3, art. 109 ust. 1 pkt 1).
  - C. Niewypłacalność, postępowanie upadłościowe lub restrukturyzacyjne (art. 109 ust. 1 pkt 4).
  - D. Inne podstawy (art. 109 ust. 1 pkt 5–10): naruszenie obowiązków zawodowych, konflikt interesów, wprowadzenie w błąd, zakaz ubiegania się o zamówienia publiczne.

  Każdą podsekcję wypełnij formułą: „Wykonawca oświadcza, że [nie podlega / nie zachodzi] [konkretna podstawa]". Nie hipotetyzuj — jeżeli wykonawca dotychczas nie miał problemów, oświadczenie jest negatywne.

- **Część IV — Kryteria kwalifikacji (Sekcja G)**. Strategia zależy od SIWZ:
  - **Gdy SIWZ.warunki_udzialu jest pusta** → użyj formy uproszczonej (sekcja α JEDZ): „Wykonawca oświadcza, że spełnia wszystkie wymagane kryteria kwalifikacji wskazane w ogłoszeniu / SIWZ". To wystarczy zgodnie z trybem podstawowym (art. 275 pkt 1).
  - **Gdy SIWZ.warunki_udzialu zawiera konkretne warunki** → wypełnij szczegółowe sekcje A-D mapując kategorie:
    - A. Kompetencje / uprawnienia → warunki z `category: kompetencje` lub `uprawnienia` z SIWZ.
    - B. Sytuacja ekonomiczna i finansowa → warunki z `category: sytuacja ekonomiczna`.
    - C. Zdolność techniczna i zawodowa → warunki z `category: zdolność techniczna`.
    - D. Systemy zarządzania jakością i środowiskowego → tylko jeżeli SIWZ wprost wymaga (rzadko w trybie krajowym).

    Dla każdego warunku z SIWZ: zacytuj warunek 1:1, dodaj formułę „Wykonawca oświadcza, że spełnia ten warunek", a następnie `[DO UZUPEŁNIENIA: <konkretny środek dowodowy — wykaz, polisa, kadra>]` zgodnie z `evidence_required` z SIWZ.

**Format wyjściowy:** dokładnie siedem bloków Markdown, każdy poprzedzony heading-iem, w tej kolejności:

```
## A. Oświadczenie o niepodleganiu wykluczeniu

<treść oświadczenia — MUSI zacząć się od identyfikacji wykonawcy w formie: "Niniejszym oświadczam w imieniu <PEŁNA NAZWA FIRMY>, NIP <FIRM_NIP>, REGON <FIRM_REGON> (jeżeli dostępny), że wykonawca nie podlega wykluczeniu z postępowania o udzielenie zamówienia publicznego pod numerem ogłoszenia BZP <BZP_NUMBER>, w szczególności art. 108 ust. 1 oraz art. 109 ust. 1 ustawy Pzp." Identyfikatory wykonawcy + numer BZP są obowiązkowe w treści tej sekcji. Sekcja A nie ma żadnego innego przeznaczenia — to konkretne oświadczenie o niepodleganiu wykluczeniu, nie oświadczenie spełnienia warunków doświadczenia ani inne.>

## B. JEDZ — Część I: Informacje dotyczące postępowania o udzielenie zamówienia oraz instytucji zamawiającej

<wypełniona Część I JEDZ>

## C. Szkic listu intencyjnego — pierwsze 2-3 akapity

<2-3 akapity formalnego listu otwierającego ofertę>

## D. Uwagi szkicownika

<jeżeli SIWZ dostępna: konkretne wymagania z SIWZ, punktowo>
<jeżeli SIWZ niedostępna: 2-5 bullet-points checklisty weryfikacyjnej>

## E. JEDZ — Część II: Informacje dotyczące wykonawcy

<podsekcje A, B, C, D wypełnione z FirmProfile>

## F. JEDZ — Część III: Powody wykluczenia

<podsekcje A, B, C, D — oświadczenia negatywne wykonawcy>

## G. JEDZ — Część IV: Kryteria kwalifikacji

<forma uproszczona α LUB szczegółowe sekcje A-D mapowane do SIWZ.warunki_udzialu>
```

Sekcje A-G są obowiązkowe, w tej kolejności. Bez dodatkowego tekstu poza tymi siedmioma sekcjami."""


# Prompt cache the system prompt — it's static across all draft calls,
# >2048 tokens after the system instruction loads, qualifies for cache.
# Reduces per-draft cost ~70% after warm-up.
SYSTEM_BLOCK = [
    {
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }
]


def _firm_block(firm: FirmProfile) -> str:
    """Compact firm profile rendering for the user prompt."""
    lines = [
        f"Pełna nazwa firmy: {firm.legal_name}",
        f"Skrócona nazwa: {firm.short_name}",
        f"NIP: {firm.nip}",
    ]
    if firm.regon:
        lines.append(f"REGON: {firm.regon}")
    if firm.krs:
        lines.append(f"KRS: {firm.krs}")
    lines.append(f"Adres siedziby: {firm.address}")
    lines.append(f"Reprezentant (osoba podpisująca ofertę): {firm.representative}")
    lines.append(f"Kontakt email: {firm.contact_email}")
    if firm.contact_phone:
        lines.append(f"Kontakt telefon: {firm.contact_phone}")
    return "\n".join(lines)


def _announcement_block(ann: TenderAnnouncement) -> str:
    """Compact structured rendering of the announcement for the prompt."""
    lines = [
        f"Numer ogłoszenia BZP: {ann.bzp_number} (wersja: {ann.notice_number.rsplit('/', 1)[-1]})",
        f"Identyfikator postępowania (OCDS): {ann.tender_id_ocds}",
        f"Data publikacji: {ann.publication_date.date().isoformat()}",
    ]
    if ann.submitting_offers_date:
        lines.append(
            f"Termin składania ofert: {fmt_pl_datetime(ann.submitting_offers_date)}"
        )
    lines.extend(
        [
            "",
            "**Zamawiający:**",
            f"  Nazwa: {ann.organization_name}",
            f"  Miasto: {ann.organization_city}",
            f"  NIP: {ann.organization_nip}",
        ]
    )
    if ann.organization_regon:
        lines.append(f"  REGON: {ann.organization_regon}")
    if ann.organization_address_street:
        lines.append(f"  Ulica: {ann.organization_address_street}")
    if ann.organization_address_postcode:
        lines.append(f"  Kod pocztowy: {ann.organization_address_postcode}")
    if ann.organization_role_description:
        lines.append(f"  Rola: {ann.organization_role_description}")
    if ann.organization_business_description:
        lines.append(f"  Działalność: {ann.organization_business_description}")

    lines.extend(
        [
            "",
            "**Przedmiot zamówienia:**",
            f"  Tytuł: {ann.order_object}",
            f"  Rodzaj: {ann.order_type}",
        ]
    )
    if ann.short_description:
        lines.append(f"  Opis: {ann.short_description}")
    if ann.cpv_main:
        lines.append(f"  Główny CPV: {ann.cpv_main.code} — {ann.cpv_main.label}")
    for cpv in ann.cpv_additional:
        lines.append(f"  Dodatkowy CPV: {cpv.code} — {cpv.label}")
    if ann.realization_period:
        lines.append(f"  Okres realizacji: {ann.realization_period}")

    if ann.criteria:
        lines.append("")
        lines.append("**Kryteria oceny ofert:**")
        for crit in ann.criteria:
            lines.append(f"  - {crit.name}: waga {crit.weight_pct:.0f}%")

    if ann.participation_conditions:
        lines.append("")
        lines.append("**Warunki udziału w postępowaniu:**")
        lines.append(f"  {ann.participation_conditions}")

    if ann.procedure_basis:
        lines.append("")
        lines.append(f"**Tryb i podstawa prawna:** {ann.procedure_basis}")

    lines.append("")
    lines.append(
        f"**Próg UE:** {'poniżej progu (krajowy)' if ann.tender_amount_below_eu else 'powyżej progu (unijny)'}"
    )

    return "\n".join(lines)


def _siwz_block(siwz: SiwzRequirements) -> str:
    """Render structured SIWZ requirements for prompt injection.

    Kept compact — drafter doesn't need the source path / model id, only
    the substantive requirements. Empty fields are written explicitly
    as "Brak" so the model can see "this is genuinely absent" vs "this
    wasn't provided to me."
    """
    lines: list[str] = []

    lines.append("**Warunki udziału w postępowaniu:**")
    if siwz.warunki_udzialu:
        for w in siwz.warunki_udzialu:
            evidence = f" [dowód: {w.evidence_required}]" if w.evidence_required else ""
            lines.append(f"  - ({w.category}) {w.text}{evidence}")
    else:
        lines.append("  Brak warunków wykraczających poza brak podstaw wykluczenia.")

    lines.append("")
    lines.append("**Kryteria oceny ofert:**")
    if siwz.kryteria_oceny:
        for k in siwz.kryteria_oceny:
            desc = f" — {k.description}" if k.description else ""
            lines.append(f"  - {k.name}: waga {k.weight_percent:.0f}%{desc}")
    else:
        lines.append("  Brak — SIWZ nie sprecyzowała kryteriów.")

    lines.append("")
    lines.append("**Wymagane dokumenty do oferty (poza JEDZ):**")
    if siwz.wymagane_dokumenty:
        for d in siwz.wymagane_dokumenty:
            lines.append(f"  - {d}")
    else:
        lines.append("  Brak dodatkowych dokumentów wskazanych w SIWZ.")

    lines.append("")
    lines.append("**Terminy:**")
    t = siwz.terminy
    lines.append(f"  Składanie ofert: {t.skladanie_ofert or 'brak w SIWZ'}")
    lines.append(f"  Otwarcie ofert: {t.otwarcie_ofert or 'brak w SIWZ'}")
    lines.append(f"  Związanie ofertą: {t.zwiazania_oferta or 'brak w SIWZ'}")
    lines.append(f"  Realizacja: {t.realizacja or 'brak w SIWZ'}")

    lines.append("")
    lines.append("**Kary umowne:**")
    if siwz.kary_umowne:
        for kara in siwz.kary_umowne:
            lines.append(f"  - {kara.trigger}: {kara.amount}")
    else:
        lines.append("  Brak kar umownych w wyciągu SIWZ (mogą być w projekcie umowy — odrębny dokument).")

    lines.append("")
    if siwz.wadium:
        form = f", forma: {siwz.wadium.form}" if siwz.wadium.form else ""
        lines.append(f"**Wadium:** {siwz.wadium.amount}{form}")
    else:
        lines.append("**Wadium:** nie wymagane.")

    if siwz.jedz_scope:
        lines.append(f"**Zakres JEDZ wymagany przez SIWZ:** części {', '.join(siwz.jedz_scope)}.")
    else:
        lines.append("**Zakres JEDZ:** SIWZ nie sprecyzowała — Phase 0 generuje Część I jako domyślną.")

    if siwz.kontakt and (siwz.kontakt.name or siwz.kontakt.email or siwz.kontakt.phone):
        bits = []
        if siwz.kontakt.name:
            bits.append(siwz.kontakt.name)
        if siwz.kontakt.email:
            bits.append(siwz.kontakt.email)
        if siwz.kontakt.phone:
            bits.append(siwz.kontakt.phone)
        lines.append(f"**Kontakt (osoba prowadząca postępowanie):** {' / '.join(bits)}")

    if siwz.additional_notes:
        lines.append("")
        lines.append("**Dodatkowe uwagi z SIWZ:**")
        for note in siwz.additional_notes:
            lines.append(f"  - {note}")

    return "\n".join(lines)


def _user_prompt(
    ann: TenderAnnouncement,
    firm: FirmProfile,
    siwz: SiwzRequirements | None = None,
) -> str:
    siwz_section = ""
    if siwz is not None:
        siwz_section = f"""

# SIWZ (wymagania ze specyfikacji)

{_siwz_block(siwz)}
"""

    return f"""# Ogłoszenie

{_announcement_block(ann)}

# Wykonawca składający ofertę

{_firm_block(firm)}{siwz_section}

# Twoje zadanie

Wygeneruj sekcje A-D zgodnie z zasadami z system promptu, używając danych z ogłoszenia, profilu wykonawcy i (jeżeli dostępne) wymagań z SIWZ. Pamiętaj: szkic do recenzji specjalisty, nie finalny dokument."""


def _estimate_cost_usd(
    model: str, input_tokens: int, cache_creation_tokens: int, cache_read_tokens: int, output_tokens: int
) -> float:
    """Approximate USD cost. Cache pricing per Anthropic docs (input class).

    Cache write (creation) = 1.25× input rate.
    Cache read              = 0.10× input rate.
    """
    in_rate, out_rate = PRICING_USD_PER_MILLION.get(model, (1.0, 5.0))
    cost = (
        input_tokens * in_rate / 1_000_000
        + cache_creation_tokens * in_rate * 1.25 / 1_000_000
        + cache_read_tokens * in_rate * 0.10 / 1_000_000
        + output_tokens * out_rate / 1_000_000
    )
    return cost


def _log_call(
    *,
    log_dir: Path,
    ann_bzp: str,
    model: str,
    input_tokens: int,
    cache_creation: int,
    cache_read: int,
    output_tokens: int,
    wall_seconds: float,
    cost_usd: float,
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{datetime.now(timezone.utc).date().isoformat()}.jsonl"
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": "draft",
        "bzp": ann_bzp,
        "model": model,
        "wall_s": round(wall_seconds, 2),
        "input_tokens": input_tokens,
        "cache_creation_tokens": cache_creation,
        "cache_read_tokens": cache_read,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd, 5),
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


_SECTION_RE = re.compile(
    # Capture `## <Letter A-G>. …` and its body. Tight `##` (no `#` or
    # `###`) so SIWZ-aware drafts emitting `### I.1) Sub-heading` (Roman
    # numeral I) inside section A stay as body content, not phantom
    # section I. Capture range A-G (Phase 0.7 added E/F/G — JEDZ Parts
    # II-IV); lookahead range A-Z so a stray `## H. Bonus` past G
    # terminates G's body and is silently dropped instead of leaking in.
    r"^##\s+([A-G])\.\s+(.+?)\n(.*?)(?=^##\s+[A-Z]\.|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _split_sections(raw_md: str) -> dict[str, str]:
    """Split the model's response into the four sections A-D.

    Returns dict keyed `"A"`, `"B"`, `"C"`, `"D"`. Missing sections
    are absent from the dict; caller decides whether to fail or stub.
    """
    out: dict[str, str] = {}
    for m in _SECTION_RE.finditer(raw_md):
        letter = m.group(1)
        body = m.group(3).strip()
        out[letter] = body
    return out


def draft_for_announcement(
    ann: TenderAnnouncement,
    firm: FirmProfile,
    *,
    siwz: SiwzRequirements | None = None,
    model: str = DEFAULT_MODEL,
    log_dir: Path | None = None,
    api_key: Optional[str] = None,
) -> DraftBundle:
    """Run one drafting pass through Anthropic. Returns DraftBundle.

    When `siwz` is provided, the drafter's Section D switches from a
    "specialist must verify" checklist to concrete citations from the
    SIWZ. When `siwz=None`, Phase 0 behavior is preserved.

    Raises `ValueError` if the model omits one of the required sections.
    """
    client = anthropic.Anthropic(api_key=api_key)
    start = time.time()

    response = client.messages.create(
        model=model,
        # 16384 (was 8192 in Phase 0.5) — JEDZ Parts II-IV (sections E,
        # F, G) added in Phase 0.7 push output to 5-7k tokens on average,
        # 9-10k when SIWZ.warunki_udzialu is rich. Headroom matters: a
        # truncated JEDZ Part IV is worse than a slow draft.
        max_tokens=16384,
        system=SYSTEM_BLOCK,  # type: ignore[arg-type]  # SDK accepts the cached-block list shape at runtime
        messages=[{"role": "user", "content": _user_prompt(ann, firm, siwz=siwz)}],
    )
    wall = time.time() - start

    # Anthropic SDK returns a list of content blocks of various types
    # (TextBlock, ThinkingBlock, ToolUseBlock, …). We only emit + want
    # plain text, so isinstance-narrow to TextBlock.
    raw_md = "".join(
        block.text for block in response.content
        if isinstance(block, anthropic.types.TextBlock)
    )

    sections = _split_sections(raw_md)
    # A, B, C are hard requirements (oświadczenie + JEDZ I + list).
    # E, F, G (JEDZ Parts II-IV) are also required since Phase 0.7.
    # D (uwagi) is required when SIWZ is provided; checklist-style when
    # not, but the prompt still asks for it — treat missing D as a fail.
    required = "ABCDEFG"
    missing = [letter for letter in required if letter not in sections]
    if missing:
        raise ValueError(
            f"Drafter omitted sections: {missing}. Raw response head: {raw_md[:200]!r}"
        )

    usage = response.usage
    cost = _estimate_cost_usd(
        model=model,
        input_tokens=usage.input_tokens,
        cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        output_tokens=usage.output_tokens,
    )

    if log_dir is not None:
        _log_call(
            log_dir=log_dir,
            ann_bzp=ann.bzp_number,
            model=model,
            input_tokens=usage.input_tokens,
            cache_creation=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
            output_tokens=usage.output_tokens,
            wall_seconds=wall,
            cost_usd=cost,
        )

    return DraftBundle(
        oswiadczenie_wykluczenie_md=sections["A"],
        jedz_czesc_1_md=sections["B"],
        list_intencyjny_md=sections["C"],
        model_notes=sections.get("D"),
        jedz_czesc_2_md=sections["E"],
        jedz_czesc_3_md=sections["F"],
        jedz_czesc_4_md=sections["G"],
    )

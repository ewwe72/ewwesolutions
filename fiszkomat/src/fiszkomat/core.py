"""fiszkomat Phase 0 pipeline: PDF -> chunks -> Haiku -> validated cards -> .apkg."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

import pypdf
from pydantic import BaseModel, Field, ValidationError

from .prompts import SYSTEM_INSTRUCTIONS, chunk_user_prompt


HAIKU_MODEL = "claude-haiku-4-5-20251001"


# ---------- data types ----------


class Card(BaseModel):
    # `z` (zajęcia / rozdział number) ranges widely across med textbooks —
    # original farmakologia skrypt had 1–10, scanned textbooks often number
    # chapters into the 20s/30s. Keep loose; 0 = "unknown chapter".
    z: int = Field(ge=0, le=99)
    t: str
    d: str
    m: str
    i: str
    c: str
    # `n` (działania niepożądane) — added in "detailed" mode for egzamin prep.
    # Optional so existing cards.json files without it remain valid (the web
    # reviewer just won't render the section if empty).
    n: str = ""


@dataclass
class ChunkPlan:
    chunk_idx: int
    page_start: int            # 1-based inclusive
    page_end: int              # 1-based inclusive
    zajecia_label: str         # e.g. "Zajęcia 3" or "Zajęcia 3–4"
    text: str                  # extracted text; empty if mode == "vision"
    pdf_bytes: bytes | None = None  # populated in vision mode; raw PDF for these pages
    mode: str = "text"         # "text" or "vision"


@dataclass
class RunStats:
    pdf_pages: int
    chunks: int
    cards_raw: int
    cards_valid: int
    cards_rejected: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    api_cost_usd: float
    wall_seconds: float


# ---------- PDF ingest + chunking ----------


_SPARSE_THRESHOLD = 50  # below this many *printable* chars, a "page" is treated as empty


class EmptyPdfError(Exception):
    """Raised when the PDF yields no extractable text on any engine.
    The run() orchestrator catches this and routes to vision-mode (Claude
    multimodal reads the PDF pages as images) — so a scanned / image-only
    PDF still produces a deck, just at higher per-page cost."""


def _printable_chars(text: str) -> int:
    """Count chars that look like real content. Excludes control characters
    (\\x00–\\x1f except tab/newline/CR) and other non-printable bytes that
    PDF text extractors emit when the text layer is broken / image-only.

    Why this matters: a 16-page scanned PDF with a 'fake' text layer
    (every "char" is \\x01) would pass a naive `len(t)`-based threshold
    check, skip vision-mode fallback, and run Haiku on garbage chunks —
    producing 0 valid cards silently. Counting *printable* chars catches
    that case correctly and triggers the OCR path in run()."""
    return sum(1 for c in text if c.isprintable() or c in "\n\r\t")


def extract_pages(pdf_path: Path) -> list[str]:
    """Return per-page text, preserving Polish diacritics.

    Tries pypdf first (fast). For any page that comes back with too few
    *printable* chars, falls back to pdfplumber (slower, sometimes catches
    text where pypdf fails on funky encodings). If BOTH engines yield
    near-empty across all pages, raises EmptyPdfError so run() can switch
    to vision-mode (OCR via Claude multimodal) instead of burning Haiku
    tokens on blank chunks."""
    reader = pypdf.PdfReader(str(pdf_path))
    pages = [page.extract_text() or "" for page in reader.pages]

    sparse_idx = [i for i, t in enumerate(pages) if _printable_chars(t) < _SPARSE_THRESHOLD]
    if sparse_idx:
        try:
            import pdfplumber
            with pdfplumber.open(str(pdf_path)) as pdf:
                for i in sparse_idx:
                    if i >= len(pdf.pages):
                        continue
                    alt = pdf.pages[i].extract_text() or ""
                    if _printable_chars(alt) > _printable_chars(pages[i]):
                        pages[i] = alt
        except Exception:
            pass  # pdfplumber missing or per-page error — keep pypdf result

    total_printable = sum(_printable_chars(t) for t in pages)
    if total_printable < _SPARSE_THRESHOLD * max(1, len(pages) // 4):
        raise EmptyPdfError(
            f"Z PDFu nie udało się wyciągnąć tekstu "
            f"({total_printable} znaków drukowanych z {len(pages)} stron). "
            f"Wygląda na skan / PDF obrazkowy lub z popsutą warstwą tekstową — "
            f"przełączam na tryb OCR."
        )
    return pages


_ZAJECIA_RE = re.compile(r"Zaj[ęe]cia\s+(\d+)\s*[.\-]", re.IGNORECASE)


def detect_zajecia(pages: list[str]) -> dict[int, int]:
    """Map 1-based page number -> zajecia number.

    The skrypt uses `Zajęcia N.` as a section header that can appear anywhere
    on a page (often after a `---` divider mid-page). For each page we record
    the LAST `Zajęcia N.` seen up to and including that page. Pages with no
    header inherit the running value."""
    mapping: dict[int, int] = {}
    current: int | None = None
    for idx, text in enumerate(pages, start=1):
        matches = list(_ZAJECIA_RE.finditer(text))
        if matches:
            current = int(matches[-1].group(1))
        if current is not None:
            mapping[idx] = current
    return mapping


def plan_chunks_vision(pdf_path: Path, pages_per_chunk: int = 5) -> list[ChunkPlan]:
    """Vision-mode chunk plan: each chunk is a slice of the original PDF, packed
    back into a smaller PDF for transmission as an Anthropic `document` block.
    Used when text extraction failed (scanned / image-only PDFs)."""
    import io
    reader = pypdf.PdfReader(str(pdf_path))
    n = len(reader.pages)
    chunks: list[ChunkPlan] = []
    chunk_idx = 0
    start = 1
    while start <= n:
        end = min(start + pages_per_chunk - 1, n)
        writer = pypdf.PdfWriter()
        for p in range(start - 1, end):
            writer.add_page(reader.pages[p])
        buf = io.BytesIO()
        writer.write(buf)
        chunks.append(ChunkPlan(
            chunk_idx=chunk_idx,
            page_start=start,
            page_end=end,
            zajecia_label=f"strony {start}-{end}",
            text="",
            pdf_bytes=buf.getvalue(),
            mode="vision",
        ))
        chunk_idx += 1
        start = end + 1
    return chunks


def plan_chunks(pages: list[str], pages_per_chunk: int = 5) -> list[ChunkPlan]:
    """Split pages into roughly equal chunks, preferring zajecia boundaries.
    Each chunk gets a label like 'Zajęcia 3' or 'Zajęcia 3–4' for the prompt."""
    zmap = detect_zajecia(pages)
    n = len(pages)
    chunks: list[ChunkPlan] = []
    start = 1
    chunk_idx = 0
    while start <= n:
        end = min(start + pages_per_chunk - 1, n)
        # If a new zajecia starts within (start, end], cut just before it
        # so chunks roughly align with zajecia boundaries.
        for p in range(start + 1, end + 1):
            cur = zmap.get(p)
            prev = zmap.get(p - 1)
            if cur is not None and prev is not None and cur != prev and (p - start) >= 2:
                end = p - 1
                break
        # Build the label
        zs_in_chunk = sorted({zmap[p] for p in range(start, end + 1) if p in zmap})
        if not zs_in_chunk:
            label = f"strony {start}-{end} (brak nagłówka zajęć)"
        elif len(zs_in_chunk) == 1:
            label = f"Zajęcia {zs_in_chunk[0]}"
        else:
            label = f"Zajęcia {zs_in_chunk[0]}–{zs_in_chunk[-1]}"
        text = "\n\n".join(pages[start - 1:end])
        chunks.append(ChunkPlan(chunk_idx, start, end, label, text))
        chunk_idx += 1
        start = end + 1
    return chunks


# ---------- Anthropic call ----------


# Haiku 4.5 pricing (USD per 1M tokens)
_PRICE_IN = 1.00
_PRICE_OUT = 5.00
_PRICE_CACHE_READ = 0.10
_PRICE_CACHE_WRITE = 1.25


def _cost(usage) -> float:
    inp = getattr(usage, "input_tokens", 0)
    out = getattr(usage, "output_tokens", 0)
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        inp * _PRICE_IN / 1e6
        + out * _PRICE_OUT / 1e6
        + cr * _PRICE_CACHE_READ / 1e6
        + cw * _PRICE_CACHE_WRITE / 1e6
    )


def generate_chunk_cards(client, chunk: ChunkPlan, max_output_tokens: int = 8192,
                          card_mode: str = "detailed") -> tuple[list[dict], object]:
    """Call Haiku for one chunk. Returns (raw_card_dicts, usage).

    `chunk.mode` controls extraction path (text vs vision); `card_mode`
    controls the card schema the model should emit:
      - "simple"   → 5-field cards (z,t,d,m,i) — lighter, kolokwium prep
      - "detailed" → 7-field cards (z,t,d,m,i,c,n) — for egzamin, includes
        contraindications and działania niepożądane; honest "Brak w książce"
        when source is silent.

    `chunk.mode` cases:
      - "text": fast/cheap path. The pre-extracted text is sent inline.
      - "vision": OCR path. The chunk's slice of the original PDF is
        attached as a `document` block; Claude OCRs internally.
    """
    if chunk.mode == "vision":
        return _generate_chunk_cards_vision(client, chunk, max_output_tokens, card_mode)
    user_msg = chunk_user_prompt(chunk.zajecia_label, chunk.text, mode=card_mode)
    resp = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=max_output_tokens,
        system=[
            {
                "type": "text",
                "text": SYSTEM_INSTRUCTIONS,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    try:
        cards = _parse_card_array(text)
    except (ValueError, json.JSONDecodeError) as e:
        # Salvage: model probably truncated. Pull whole `{...}` objects from the buffer.
        cards = _salvage_card_objects(text)
        if not cards:
            raise
    return cards, resp.usage


def _generate_chunk_cards_vision(client, chunk: ChunkPlan, max_output_tokens: int,
                                  card_mode: str = "detailed") -> tuple[list[dict], object]:
    """OCR path: send the chunk's PDF slice as a `document` content block.
    Claude reads (and silently OCRs) the pages, then emits the same JSON
    schema as the text path."""
    import base64
    assert chunk.pdf_bytes is not None, "vision chunk missing pdf_bytes"
    pdf_b64 = base64.b64encode(chunk.pdf_bytes).decode("ascii")
    mode_tag = "SIMPLE" if card_mode == "simple" else "DETAILED"
    user_text = (
        f"TRYB: {mode_tag}\n"
        f"FRAGMENT SKRYPTU — {chunk.zajecia_label}\n"
        "Treść fragmentu jest w załączonym pliku PDF (zeskanowane strony — "
        "odczytaj tekst z obrazów). Wygeneruj fiszki dla grup farmakologicznych "
        f"opisanych w tym fragmencie, stosując schemat {mode_tag} i te same "
        "zasady stylistyczne co dla wcześniejszych przykładów. Pamiętaj o "
        "regule 'Brak w książce' dla pól c/n gdy źródło ich nie podaje.\n\n"
        "Jeśli skan jest tak słabej jakości że nie da się odczytać sensownej "
        "treści — zwróć pustą tablicę `[]`, nie próbuj zgadywać."
    )
    resp = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=max_output_tokens,
        system=[
            {
                "type": "text",
                "text": SYSTEM_INSTRUCTIONS,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_b64,
                    },
                },
                {"type": "text", "text": user_text},
            ],
        }],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    try:
        cards = _parse_card_array(text)
    except (ValueError, json.JSONDecodeError):
        cards = _salvage_card_objects(text)
        if not cards:
            raise
    return cards, resp.usage


_OBJ_RE = re.compile(
    r'\{\s*"z"\s*:\s*\d+\s*,\s*"t"\s*:\s*"[^"]*"\s*,\s*"d"\s*:\s*"[^"]*"\s*,\s*"m"\s*:\s*"[^"]*"\s*,\s*"i"\s*:\s*"[^"]*"\s*,\s*"c"\s*:\s*"[^"]*"\s*\}',
    re.DOTALL,
)


def _salvage_card_objects(text: str) -> list[dict]:
    """Pull individual card objects from a partial/malformed JSON response."""
    out: list[dict] = []
    for m in _OBJ_RE.finditer(text):
        try:
            out.append(json.loads(m.group(0)))
        except json.JSONDecodeError:
            continue
    return out


def _parse_card_array(text: str) -> list[dict]:
    """Robust-ish JSON extraction: strip code fences, find first [...] block."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    start = t.find("[")
    end = t.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON array found in model output (head: {text[:300]!r})")
    return json.loads(t[start:end + 1])


# ---------- validation ----------


def validate_cards(raw_cards: list[dict]) -> tuple[list[Card], list[tuple[dict, str]]]:
    """Return (valid, rejected_with_reasons). Dedup by hash of (t, d) within this run."""
    valid: list[Card] = []
    rejected: list[tuple[dict, str]] = []
    seen: set[str] = set()
    for raw in raw_cards:
        try:
            card = Card(**raw)
        except ValidationError as e:
            rejected.append((raw, f"schema: {e.errors()[0]['msg']}"))
            continue
        key = hashlib.sha1(
            (card.t.strip().lower() + "|" + card.d.strip().lower()).encode("utf-8")
        ).hexdigest()
        if key in seen:
            rejected.append((raw, "duplicate (same t+d)"))
            continue
        if len(card.t) > 200:
            rejected.append((raw, "t too long"))
            continue
        if len(card.m) > 800 or len(card.i) > 600 or len(card.c) > 600 or len(card.n) > 800:
            rejected.append((raw, "field too long"))
            continue
        # crude English leakage detector — Polish skrypt should be Polish
        eng_hits = sum(1 for w in (" the ", " and ", " with ", " of ", " is ") if w in (" " + card.m.lower() + " "))
        if eng_hits >= 2:
            rejected.append((raw, "English leakage suspected"))
            continue
        seen.add(key)
        valid.append(card)
    return valid, rejected


# ---------- Anki pack ----------


def pack_apkg(cards: Iterable[Card], deck_name: str, out_path: Path) -> None:
    import genanki

    # NOTE: bumped model ID after schema change (added Dzialania field). Anki
    # treats model-ID as the deck schema fingerprint — keeping the old ID with
    # new fields would corrupt existing imports. New ID = fresh model.
    model = genanki.Model(
        1735101011,
        "fiszkomat — Farmakologia v2",
        fields=[
            {"name": "Tytul"},
            {"name": "Leki"},
            {"name": "Mechanizm"},
            {"name": "Wskazania"},
            {"name": "Przeciwwskazania"},
            {"name": "Dzialania"},
            {"name": "Zajecia"},
        ],
        templates=[
            {
                # Front: drugs only (the prompt). Back: group name + mechanism
                # + indications + contraindications + (optionally) działania
                # niepożądane.
                # Per med-student feedback 2026-05-13: showing the group name
                # on the front gave away the answer; the hardest part of a
                # pharma kolokwium is *associating a drug with its group*.
                # Działania niepożądane are rendered only when non-empty
                # (Anki's {{#Field}}...{{/Field}} conditional — empty in
                # simple-mode cards, present in detailed-mode cards).
                "name": "Leki -> grupa + reszta",
                "qfmt": (
                    "<div class='zaj'>Zajęcia {{Zajecia}}</div>"
                    "<div class='tytul'>{{Leki}}</div>"
                ),
                "afmt": (
                    "<div class='zaj'>Zajęcia {{Zajecia}}</div>"
                    "<div class='tytul'>{{Tytul}}</div>"
                    "<hr>"
                    "<div class='label'>Mechanizm</div><div>{{Mechanizm}}</div>"
                    "<div class='label'>Wskazania</div><div>{{Wskazania}}</div>"
                    "{{#Przeciwwskazania}}<div class='label'>Przeciwwskazania</div><div>{{Przeciwwskazania}}</div>{{/Przeciwwskazania}}"
                    "{{#Dzialania}}<div class='label'>Działania niepożądane</div><div>{{Dzialania}}</div>{{/Dzialania}}"
                ),
            },
        ],
        css=(
            ".card{font-family:Inter,Arial,sans-serif;font-size:18px;color:#1a1814;"
            "background:#faf6ec;padding:20px;line-height:1.5;}"
            ".zaj{font-family:JetBrains Mono,monospace;font-size:11px;letter-spacing:.18em;"
            "text-transform:uppercase;color:#8a8275;margin-bottom:8px;}"
            ".tytul{font-family:Cormorant Garamond,Georgia,serif;font-weight:600;"
            "font-size:28px;line-height:1.1;color:#7a1f1f;margin-bottom:10px;}"
            ".leki{font-style:italic;color:#4a443a;margin-bottom:6px;}"
            ".label{font-family:JetBrains Mono,monospace;font-size:11px;letter-spacing:.18em;"
            "text-transform:uppercase;color:#a87b3a;margin-top:12px;margin-bottom:4px;}"
        ),
    )

    deck = genanki.Deck(int(hashlib.sha1(deck_name.encode()).hexdigest()[:8], 16), deck_name)
    for card in cards:
        note = genanki.Note(
            model=model,
            fields=[card.t, card.d, card.m, card.i, card.c, card.n, str(card.z)],
            tags=[f"fiszkomat", f"zajecia::{card.z}"],
        )
        deck.add_note(note)
    genanki.Package(deck).write_to_file(str(out_path))


# ---------- orchestration ----------


QUALITY_REVIEW_MODEL = "claude-opus-4-7"


def quality_review_pass(client, cards: list[Card], card_mode: str = "simple",
                        log=print) -> tuple[list[Card], float]:
    """Optional second pass — sends the full deck produced by Haiku to
    Claude Opus 4.7 for a pharmacology-quality review. The reviewer must:

      - Catch incorrect drug groupings (e.g. neuroleptyk listed under
        M-antagonists)
      - Correct mechanism statements that name wrong receptors / enzymes
      - Fix indications that don't match the drug class
      - Remove duplicate / overlapping cards
      - Preserve cards that are already correct

    Output schema is identical to Haiku's. Returns the corrected deck
    plus the Opus call's USD cost."""
    if not cards:
        return cards, 0.0
    deck_in = [c.model_dump() for c in cards]
    log(f"  → quality review pass on {len(cards)} cards (Opus 4.7)...")

    schema_note = ('"z","t","d","m","i"' if card_mode == "simple"
                   else '"z","t","d","m","i","c","n"')
    system_text = (
        "Jesteś polskim profesorem farmakologii. Recenzujesz fiszki Anki "
        "przygotowane przez asystenta dla studentów medycyny. "
        "Twoje zadanie: przejrzeć całą talię i poprawić merytoryczne błędy.\n\n"
        "ZASADY REWIZJI:\n"
        "1. Sprawdź, czy każda grupa farmakologiczna ma poprawnie zgrupowane leki — "
        "leki z innej klasy farmakologicznej w polu 'd' to BŁĄD (np. neuroleptyk "
        "w 'antagonistach receptorów muskarynowych').\n"
        "2. Mechanizm działania (pole 'm') musi opisywać poprawny receptor / enzym / "
        "szlak metaboliczny. Zwróć uwagę na klasyczne pomyłki: agoniści vs antagoniści, "
        "wzmacniacze vs inhibitory, M vs N receptory cholinergiczne, β1 vs β2.\n"
        "3. Wskazania (pole 'i') muszą być zgodne z faktyczną klasą leku. Nie wymyślaj "
        "wskazań, których lek nie ma.\n"
        "4. Usuń DUPLIKATY (te same leki w 2+ kartach z innymi tytułami grup).\n"
        "5. Karty bez błędów ZOSTAW BEZ ZMIAN — nie 'poprawiaj' jakości stylu, "
        "nie dodawaj słów. Edytuj wyłącznie tam, gdzie jest faktyczny błąd.\n"
        "6. Polski na wyjściu (poza nazwami INN leków).\n"
        "7. Nie zmieniaj numeru zajęć (pole 'z').\n\n"
        f"SCHEMAT: tablica obiektów z polami {schema_note}.\n"
        "WYJŚCIE: dokładnie ta tablica JSON z poprawkami zastosowanymi w miejscu, "
        "bez komentarzy, bez markdown fences, bez prefiksu."
    )

    user_text = (
        "Talia do recenzji (JSON):\n\n"
        + json.dumps(deck_in, ensure_ascii=False, indent=2)
    )

    resp = client.messages.create(
        model=QUALITY_REVIEW_MODEL,
        max_tokens=16384,
        system=[{"type": "text", "text": system_text}],
        messages=[{"role": "user", "content": user_text}],
    )
    usage = resp.usage
    out_text = "".join(b.text for b in resp.content if hasattr(b, "text"))

    # Opus 4.7 pricing (USD per 1M tokens): input $15, output $75. Cache pricing
    # ignored for one-shot review.
    in_tok = getattr(usage, "input_tokens", 0)
    out_tok = getattr(usage, "output_tokens", 0)
    cost = (in_tok * 15.0 + out_tok * 75.0) / 1_000_000

    try:
        reviewed_raw = _parse_card_array(out_text)
    except Exception as e:
        log(f"     ! review pass parse error: {e}; falling back to Haiku output")
        return cards, cost

    reviewed, rejected = validate_cards(reviewed_raw)
    log(f"  → review pass: {len(reviewed)} valid, {len(rejected)} rejected, ${cost:.4f}")
    if not reviewed:
        log("     ! empty review output — falling back to Haiku")
        return cards, cost
    return reviewed, cost


def run(pdf_path: Path, out_path: Path, pages_per_chunk: int = 5, dry_run: bool = False,
        max_chunks: int | None = None, log=print, card_mode: str = "simple",
        quality_pass: bool = False) -> RunStats:
    """card_mode = "simple" (5 fields, default for kolokwium) or "detailed"
    (7 fields including przeciwwskazania + działania niepożądane, for egzamin).
    quality_pass = if True, after Haiku finishes the deck is sent to Claude
    Opus 4.7 for a single review pass (factual / grouping corrections only)."""
    t0 = time.time()
    # Try the cheap text path; fall through to vision/OCR path if the PDF is
    # scanned/image-only. Auto-route is transparent to the caller — the only
    # observable difference is higher cost per chunk on vision-mode runs.
    pdf_pages_count = len(pypdf.PdfReader(str(pdf_path)).pages)
    try:
        pages = extract_pages(pdf_path)
        chunks = plan_chunks(pages, pages_per_chunk=pages_per_chunk)
        mode = "text"
    except EmptyPdfError as e:
        log(f"  text extraction failed: {e}")
        log(f"  → switching to OCR (vision) mode — koszt będzie wyższy")
        chunks = plan_chunks_vision(pdf_path, pages_per_chunk=pages_per_chunk)
        mode = "vision"
    log(f"PDF: {pdf_path.name} — {pdf_pages_count} pages -> {len(chunks)} chunks [extract={mode}, cards={card_mode}]")
    for c in chunks:
        size_hint = f"{len(c.text)} chars" if c.mode == "text" else f"{(len(c.pdf_bytes or b''))//1024} kB pdf"
        log(f"  chunk {c.chunk_idx}: pp.{c.page_start}-{c.page_end} [{c.zajecia_label}] {size_hint}")

    if dry_run:
        return RunStats(
            pdf_pages=pdf_pages_count, chunks=len(chunks),
            cards_raw=0, cards_valid=0, cards_rejected=0,
            input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0,
            api_cost_usd=0.0, wall_seconds=time.time() - t0,
        )

    from anthropic import Anthropic
    client = Anthropic()  # picks up ANTHROPIC_API_KEY from env

    raw_all: list[dict] = []
    cost_total = 0.0
    in_tok = out_tok = cr_tok = cw_tok = 0
    target_chunks = chunks if max_chunks is None else chunks[:max_chunks]
    for c in target_chunks:
        log(f"  -> calling Haiku for chunk {c.chunk_idx} ({c.zajecia_label}) ...")
        try:
            cards, usage = generate_chunk_cards(client, c, card_mode=card_mode)
        except Exception as e:
            log(f"     ERROR: {e}")
            continue
        in_tok += getattr(usage, "input_tokens", 0)
        out_tok += getattr(usage, "output_tokens", 0)
        cr_tok += getattr(usage, "cache_read_input_tokens", 0) or 0
        cw_tok += getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost = _cost(usage)
        cost_total += cost
        log(f"     -> {len(cards)} raw cards, ${cost:.4f}, "
            f"in={getattr(usage, 'input_tokens', 0)} out={getattr(usage, 'output_tokens', 0)} "
            f"cache_r={cr_tok} cache_w={cw_tok}")
        raw_all.extend(cards)

    valid, rejected = validate_cards(raw_all)
    log(f"validation: {len(valid)} valid, {len(rejected)} rejected")
    for raw, reason in rejected[:10]:
        log(f"  rejected ({reason}): t={raw.get('t', '?')!r}")

    if quality_pass and valid:
        valid, review_cost = quality_review_pass(client, valid, card_mode=card_mode, log=log)
        cost_total += review_cost

    deck_name = pdf_path.stem
    pack_apkg(valid, deck_name=f"fiszkomat — {deck_name}", out_path=out_path)
    log(f"wrote {out_path}")
    # Also persist the cards as JSON so the web reviewer can load them.
    cards_json_path = out_path.with_suffix(".cards.json")
    save_cards_json(valid, cards_json_path)
    log(f"wrote {cards_json_path}")

    return RunStats(
        pdf_pages=pdf_pages_count, chunks=len(target_chunks),
        cards_raw=len(raw_all), cards_valid=len(valid), cards_rejected=len(rejected),
        input_tokens=in_tok, output_tokens=out_tok,
        cache_read_tokens=cr_tok, cache_creation_tokens=cw_tok,
        api_cost_usd=cost_total, wall_seconds=time.time() - t0,
    )


def save_cards_json(cards: list[Card], path: Path) -> None:
    path.write_text(
        json.dumps([c.model_dump() for c in cards], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

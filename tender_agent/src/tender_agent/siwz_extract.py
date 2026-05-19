"""SIWZ (Specyfikacja Warunków Zamówienia) → structured requirements.

The Phase 0 prototype drafted bid responses from BZP announcement
metadata only — Section D of every draft says "specjalista musi
zweryfikować". This module fills that gap: pymupdf for PDF text +
stdlib zipfile for DOCX text (no new dependency), Claude (Haiku 4.5
default, prompt-cached system) to populate a `SiwzRequirements` model
that the drafter can cite line-by-line.

Two entry points:

  extract_text(doc_path)         → str    (pure pymupdf or stdlib, no LLM)
  extract_requirements(pdf_path) → SiwzRequirements

Accepts .pdf and .docx. Legacy .doc requires libreoffice conversion;
not auto-handled by this module (Phase 1 gap — see HANDOFF.md).

Plus a CLI:

  python -m tender_agent.siwz_extract <doc-path> --out <json-path>

Cost discipline: same JSONL log shape as `draft.py` (`_logs/<date>.jsonl`)
so the existing `jq` post-mortem keeps working. Haiku 4.5 default keeps
the per-SIWZ cost in the $0.03-0.05 range at typical sizes (24-25
pages of PDF text, ~30k input tokens).

Hard rule on the prompt: never fabricate. Fields not present in the
SIWZ become None / empty list. The drafter relies on `None` as a real
signal — inventing a wadium amount when none is required is worse than
nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, cast

import anthropic
import pymupdf

from .models import SiwzRequirements


# Mirrors `draft.py` PRICING_USD_PER_MILLION; keeping a local copy avoids
# importing the drafter (which carries unrelated module state).
PRICING_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5":  (1.00,  5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-7":  (15.00, 75.00),
}

DEFAULT_MODEL = os.environ.get("TENDER_AGENT_MODEL", "claude-haiku-4-5")

# Max output tokens — SIWZ extraction returns a JSON tool-input,
# realistic upper bound ~4-6k tokens for a heavy SIWZ with many
# warunki + kary_umowne. Cap at 8k to leave headroom without blowing
# cost on hallucinated padding.
MAX_OUTPUT_TOKENS = 8192


SYSTEM_PROMPT = """Jesteś asystentem polskiego specjalisty od zamówień publicznych. Twoja praca to ekstrakcja **strukturalnych wymagań** z dokumentu SIWZ (Specyfikacja Warunków Zamówienia).

SIWZ to dokument na 30-80 stron, który publikuje zamawiający. Zawiera:
- warunki udziału w postępowaniu (kto może złożyć ofertę)
- kryteria oceny ofert (cena, gwarancja, doświadczenie itd. + wagi)
- wymagane dokumenty/załączniki do oferty
- terminy (składanie ofert, otwarcie, związanie ofertą, realizacja)
- kary umowne (za zwłokę, odstąpienie, niewykonanie)
- dane kontaktowe osoby prowadzącej postępowanie
- wadium (kwotę i formę), jeżeli wymagane
- zakres JEDZ (które części wykonawca musi wypełnić)

Twoje zadanie: wczytać tekst SIWZ i zwrócić wszystkie powyższe wymagania w ustrukturyzowanej formie poprzez wywołanie narzędzia `record_siwz_requirements`.

**KRYTYCZNA ZASADA — ZAKAZ HALUCYNACJI:**

Jeżeli pole nie występuje w tekście SIWZ, zostaw je puste/null — **nie zmyślaj**:
- brak wadium w tekście → `wadium: null` (NIE wymyślaj kwoty)
- brak warunków udziału → `warunki_udzialu: []` (pusta lista)
- brak kontaktu → `kontakt: null`
- brak kar umownych → `kary_umowne: []`

Specjalista, który będzie korzystać z twojego wyniku, traktuje `null`/`[]` jako rzetelny sygnał "tego SIWZ nie wymaga". Wymyślanie wartości łamie cały sens tego narzędzia.

**Język wyników:** wyłącznie polski, w rejestrze kancelaryjnym SIWZ. Cytuj treść 1:1 ze źródła (zachowuj sformułowania typu „uprzejmie", „niniejszym", „obowiązani", „oświadczamy"). Nie tłumacz, nie parafrazuj zbędnie. Krótkie, precyzyjne sformułowania.

**Kategoryzacja warunków udziału:** każdy warunek przypisz do jednej z kategorii:
- `zdolność techniczna` — wykaz usług, sprzęt, kadra, certyfikaty techniczne
- `sytuacja ekonomiczna` — obrót, polisa OC, zdolność kredytowa
- `kompetencje` — branżowe uprawnienia ogólne, doświadczenie firmowe
- `uprawnienia` — koncesje, licencje, wpisy do rejestrów (np. RWUP)
- `inna` — wszystko, co nie pasuje do powyższych

**Konwencja kwot:** wartości pieniężne cytuj jako tekst ze źródła, łącznie z walutą i jednostką (np. „50 000 PLN", „5% wartości umowy"). Nie konwertuj.

**Konwencja terminów:** cytuj tekst ze źródła. Jeżeli SIWZ podaje datę+godzinę → oba; jeżeli tylko datę → tylko datę; jeżeli „X dni od podpisania umowy" → ten string.

**Format wyjściowy:** wywołanie narzędzia `record_siwz_requirements` z argumentami zgodnymi z zadanym schematem. Bez dodatkowego tekstu poza tym wywołaniem narzędzia."""


SYSTEM_BLOCK = [
    {
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }
]


# Tool schema — Anthropic SDK extracts structured JSON via tool_use.
# Keep this aligned 1:1 with `SiwzRequirements` (minus the provenance
# fields, which the caller fills server-side).
EXTRACT_TOOL: dict[str, Any] = {
    "name": "record_siwz_requirements",
    "description": (
        "Zapisz wyekstrahowane wymagania SIWZ. Wywołaj raz, z kompletem "
        "argumentów. Brak pól (null/puste listy) gdy SIWZ nie precyzuje."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "warunki_udzialu": {
                "type": "array",
                "description": "Warunki udziału w postępowaniu (lista).",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": (
                                "Pełna treść warunku, 1:1 ze SIWZ lub bardzo "
                                "zbliżona parafraza zachowująca liczby/progi."
                            ),
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "zdolność techniczna",
                                "sytuacja ekonomiczna",
                                "kompetencje",
                                "uprawnienia",
                                "inna",
                            ],
                        },
                        "evidence_required": {
                            "type": ["string", "null"],
                            "description": (
                                "Dokument potwierdzający spełnienie warunku, "
                                "np. 'wykaz usług + dowody należytego wykonania'. "
                                "null jeżeli SIWZ nie określiło."
                            ),
                        },
                    },
                    "required": ["text", "category", "evidence_required"],
                },
            },
            "kryteria_oceny": {
                "type": "array",
                "description": "Kryteria oceny ofert (cena + pozostałe).",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "weight_percent": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 100,
                        },
                        "description": {"type": ["string", "null"]},
                    },
                    "required": ["name", "weight_percent", "description"],
                },
            },
            "wymagane_dokumenty": {
                "type": "array",
                "description": (
                    "Obowiązkowe załączniki do oferty (poza JEDZ). "
                    "Krótki opis każdego, np. 'odpis z KRS', "
                    "'polisa OC z sumą gwarancyjną min. 100 000 PLN'."
                ),
                "items": {"type": "string"},
            },
            "terminy": {
                "type": "object",
                "properties": {
                    "skladanie_ofert": {"type": ["string", "null"]},
                    "otwarcie_ofert": {"type": ["string", "null"]},
                    "zwiazania_oferta": {"type": ["string", "null"]},
                    "realizacja": {"type": ["string", "null"]},
                },
                "required": [
                    "skladanie_ofert",
                    "otwarcie_ofert",
                    "zwiazania_oferta",
                    "realizacja",
                ],
            },
            "kary_umowne": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "trigger": {"type": "string"},
                        "amount": {"type": "string"},
                    },
                    "required": ["trigger", "amount"],
                },
            },
            "kontakt": {
                "type": ["object", "null"],
                "properties": {
                    "name": {"type": ["string", "null"]},
                    "email": {"type": ["string", "null"]},
                    "phone": {"type": ["string", "null"]},
                },
                "required": ["name", "email", "phone"],
            },
            "jedz_scope": {
                "type": "array",
                "description": (
                    "Lista rzymskich numerów części JEDZ wymaganych przez SIWZ, "
                    "np. ['I', 'III']. Pusta lista gdy SIWZ nie precyzuje."
                ),
                "items": {"type": "string"},
            },
            "wadium": {
                "type": ["object", "null"],
                "properties": {
                    "amount": {"type": "string"},
                    "form": {"type": ["string", "null"]},
                },
                "required": ["amount", "form"],
            },
            "additional_notes": {
                "type": "array",
                "description": (
                    "Inne istotne informacje, których nie da się dopasować do "
                    "pól powyżej (klauzule RODO, wizje lokalne, szczególne "
                    "wymagania techniczne itp.). Krótkie bullet-points."
                ),
                "items": {"type": "string"},
            },
        },
        "required": [
            "warunki_udzialu",
            "kryteria_oceny",
            "wymagane_dokumenty",
            "terminy",
            "kary_umowne",
            "kontakt",
            "jedz_scope",
            "wadium",
            "additional_notes",
        ],
    },
}


def extract_text(doc_path: Path) -> str:
    """Pure text extraction from a PDF or DOCX SIWZ.

    Returns the entire document as one string with page markers
    `\\n--- page N ---\\n` between pages (PDF) or paragraph-grouped
    blocks separated by blank lines (DOCX — no native page concept).

    No LLM, no network — usable as a fast smoke-test before the
    paid extraction call.

    Some contracting authorities publish the SIWZ as .docx (especially
    via e-Zamówienia where the editable template is the canonical
    upload). DOCX support uses stdlib `zipfile` + `xml.etree`; no new
    dependency.
    """
    if not doc_path.exists():
        raise FileNotFoundError(f"SIWZ document not found: {doc_path}")

    suffix = doc_path.suffix.lower()
    if suffix == ".pdf":
        return _extract_text_pdf(doc_path)
    if suffix == ".docx":
        return _extract_text_docx(doc_path)
    raise ValueError(
        f"unsupported SIWZ format {suffix!r} for {doc_path.name} — "
        "only .pdf and .docx supported (legacy .doc requires libreoffice conversion)"
    )


def _extract_text_pdf(pdf_path: Path) -> str:
    parts: list[str] = []
    # pymupdf is untyped — silence mypy strict on the API surface itself,
    # cast results back to typed values at the boundary.
    with pymupdf.open(pdf_path) as doc:  # type: ignore[no-untyped-call]
        for page_index, page in enumerate(doc, start=1):
            parts.append(f"\n--- page {page_index} ---\n")
            parts.append(cast(str, page.get_text("text")))
    return "".join(parts)


# DOCX is a ZIP containing `word/document.xml`; paragraph elements
# are `<w:p>` with `<w:t>` text runs inside. We pull the visible text
# only — ignoring footnotes, comments, headers/footers — which matches
# what a SIWZ reader would see in MS Word's main pane.
_DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def _extract_text_docx(docx_path: Path) -> str:
    import xml.etree.ElementTree as ET
    import zipfile

    with zipfile.ZipFile(docx_path) as zf:
        with zf.open("word/document.xml") as fh:
            tree = ET.parse(fh)

    parts: list[str] = []
    para_idx = 0
    for paragraph in tree.iter(f"{{{_DOCX_NS['w']}}}p"):
        runs = [
            t.text or ""
            for t in paragraph.iter(f"{{{_DOCX_NS['w']}}}t")
        ]
        text = "".join(runs).strip()
        if text:
            para_idx += 1
            # Page-marker analog every ~40 paragraphs so the LLM keeps
            # bearings on long DOCX SIWZ. Not a real page boundary —
            # docstring up top says so.
            if para_idx % 40 == 1:
                parts.append(f"\n--- block {para_idx // 40 + 1} ---\n")
            parts.append(text)
            parts.append("\n")
    return "".join(parts)


def _page_count(doc_path: Path) -> int:
    """Page count for PDF; for DOCX returns the count of non-empty paragraphs
    (the closest stable analog — DOCX has no page concept until rendered).
    Cost log records this number so the JSONL stays apples-to-apples per
    document, even if the unit isn't strictly pages for DOCX inputs.
    """
    if doc_path.suffix.lower() == ".docx":
        import xml.etree.ElementTree as ET
        import zipfile

        with zipfile.ZipFile(doc_path) as zf:
            with zf.open("word/document.xml") as fh:
                tree = ET.parse(fh)
        return sum(
            1
            for p in tree.iter(f"{{{_DOCX_NS['w']}}}p")
            if "".join((t.text or "") for t in p.iter(f"{{{_DOCX_NS['w']}}}t")).strip()
        )
    with pymupdf.open(doc_path) as doc:  # type: ignore[no-untyped-call]
        return int(doc.page_count)


def _estimate_cost_usd(
    model: str,
    input_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
    output_tokens: int,
) -> float:
    """Mirror of `draft._estimate_cost_usd` — keep formulas identical."""
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
    pdf_path: Path,
    model: str,
    pages: int,
    input_tokens: int,
    cache_creation: int,
    cache_read: int,
    output_tokens: int,
    wall_seconds: float,
    cost_usd: float,
) -> None:
    """Append one JSONL line — same shape as `draft._log_call` so existing
    `jq` post-mortem queries keep working. `kind` distinguishes drafts
    from extractions.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{datetime.now(timezone.utc).date().isoformat()}.jsonl"
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": "siwz_extract",
        "pdf": str(pdf_path),
        "pages": pages,
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


def _resolve_api_key(api_key: Optional[str]) -> Optional[str]:
    """Match the `cli.py` discovery order: arg → env → tender_agent/.env
    → invoice_idp/.env. Phase 0 shares one Anthropic key across the
    playspace, so callers can defer to this.
    """
    if api_key:
        return api_key
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        return env_key
    repo_root = Path(__file__).resolve().parents[3]
    for env_path in [
        repo_root / "tender_agent" / ".env",
        repo_root / "invoice_idp" / ".env",
    ]:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return None


def _user_prompt(pdf_text: str, pdf_path: Path, pages: int) -> str:
    return f"""# Dokument SIWZ

Plik: {pdf_path.name}
Liczba stron: {pages}

## Treść SIWZ (extrakcja pymupdf, znaczniki stron `--- page N ---`)

{pdf_text}

---

# Twoje zadanie

Wywołaj narzędzie `record_siwz_requirements` z kompletem argumentów wyciągniętych z powyższego SIWZ. Pamiętaj o zasadzie zakazu halucynacji: pola, których SIWZ nie precyzuje, ustaw na null albo pustą listę."""


def extract_requirements(
    pdf_path: Path,
    *,
    model: str = DEFAULT_MODEL,
    cost_log: Path | None = None,
    api_key: Optional[str] = None,
) -> SiwzRequirements:
    """pymupdf + Claude extraction → SiwzRequirements.

    `cost_log` is a directory (matching `draft.py`'s `log_dir` semantics)
    where the per-day JSONL log lands. `None` skips logging.

    Raises `ValueError` when the model fails to call the tool (rare —
    happens when the PDF is empty or the prompt-cache is stale).
    """
    pdf_path = pdf_path.resolve()
    pages = _page_count(pdf_path)
    pdf_text = extract_text(pdf_path)

    resolved_key = _resolve_api_key(api_key)
    client = anthropic.Anthropic(api_key=resolved_key)

    start = time.time()
    # `system` (cached-block list), `tools`, and `tool_choice` together
    # don't satisfy any single Anthropic SDK overload signature at the
    # type-checker layer, but the runtime accepts them. Suppress the
    # overload error with a single targeted ignore.
    response = client.messages.create(  # type: ignore[call-overload]
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM_BLOCK,
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": EXTRACT_TOOL["name"]},
        messages=[{"role": "user", "content": _user_prompt(pdf_text, pdf_path, pages)}],
    )
    wall = time.time() - start

    # Find the tool_use block. We forced `tool_choice` to our tool, so
    # there must be exactly one — but guard against SDK shape changes.
    tool_input: dict[str, Any] | None = None
    for block in response.content:
        if isinstance(block, anthropic.types.ToolUseBlock) and block.name == EXTRACT_TOOL["name"]:
            tool_input = cast(dict[str, Any], block.input)
            break

    if tool_input is None:
        text_blocks = "".join(
            block.text for block in response.content
            if isinstance(block, anthropic.types.TextBlock)
        )
        raise ValueError(
            f"Extractor model {model} did not call record_siwz_requirements. "
            f"Text fallback head: {text_blocks[:300]!r}"
        )

    usage = response.usage
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cost = _estimate_cost_usd(
        model=model,
        input_tokens=usage.input_tokens,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        output_tokens=usage.output_tokens,
    )

    if cost_log is not None:
        _log_call(
            log_dir=cost_log,
            pdf_path=pdf_path,
            model=model,
            pages=pages,
            input_tokens=usage.input_tokens,
            cache_creation=cache_creation,
            cache_read=cache_read,
            output_tokens=usage.output_tokens,
            wall_seconds=wall,
            cost_usd=cost,
        )

    # Pydantic validates the tool input shape against the model. If
    # the model snuck in extra fields or the wrong type, this raises
    # before the caller sees garbage.
    requirements = SiwzRequirements(
        **tool_input,
        source_pdf_path=str(pdf_path),
        extraction_model=model,
        pages_extracted=pages,
    )
    return requirements


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tender_agent.siwz_extract",
        description="Extract structured requirements from a SIWZ PDF.",
    )
    p.add_argument("pdf_path", help="Path to the SIWZ PDF.")
    p.add_argument(
        "--out",
        help="Where to write the SiwzRequirements JSON. Default: <pdf>.siwz.json.",
        default=None,
    )
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Anthropic model id (default: {DEFAULT_MODEL}).",
    )
    p.add_argument(
        "--log-dir",
        default=None,
        help=(
            "Directory for the JSONL cost log. Default: "
            "<repo>/tender_agent/_logs/."
        ),
    )
    p.add_argument(
        "--text-only",
        action="store_true",
        help="Skip the LLM call; just dump pymupdf text to stdout.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    pdf_path = Path(args.pdf_path).resolve()

    if args.text_only:
        sys.stdout.write(extract_text(pdf_path))
        return 0

    if args.log_dir:
        log_dir: Path | None = Path(args.log_dir)
    else:
        log_dir = Path(__file__).resolve().parents[3] / "tender_agent" / "_logs"

    requirements = extract_requirements(pdf_path, model=args.model, cost_log=log_dir)

    out_path = Path(args.out) if args.out else pdf_path.with_suffix(".siwz.json")
    out_path.write_text(
        requirements.model_dump_json(indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {out_path}")
    print(
        f"  warunki_udzialu={len(requirements.warunki_udzialu)} "
        f"kryteria_oceny={len(requirements.kryteria_oceny)} "
        f"wymagane_dokumenty={len(requirements.wymagane_dokumenty)} "
        f"kary_umowne={len(requirements.kary_umowne)} "
        f"wadium={'yes' if requirements.wadium else 'no'} "
        f"kontakt={'yes' if requirements.kontakt else 'no'} "
        f"pages={requirements.pages_extracted}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

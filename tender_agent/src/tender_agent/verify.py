"""Verifier sub-agent — catches hallucinations in DraftBundle outputs.

Two-tier verification:

1. **Deterministic checks** (regex/string, no LLM, no network):
   - Firm legal name + short name must appear in the draft verbatim.
   - Near-miss detection: target with one character deleted (catches the
     `PrykładIT → PrzykładIT` typo class) or one Polish diacritic
     stripped (catches `Lapy → Łapy`).
   - NIP / REGON / BZP-number values must match the source 1:1.
   - Any 10-digit token labeled `NIP:` must equal firm.nip *or*
     announcement.organization_nip — nothing else.

2. **LLM cross-check** (Haiku 4.5, tool-use-forced JSON):
   - Section D citations (when SIWZ context was used) must trace back
     to fields in `SiwzRequirements`. Any quoted SIWZ fact that doesn't
     appear in the structured requirements is flagged.
   - When SIWZ is absent, the LLM call is **skipped** — there's no
     ground truth to verify against, and Phase 0 Section D is explicit
     prose checklist (verification is the specialist's job by design).

Output: `VerificationReport` with a flat list of `Finding`s. Each
finding has severity (error / warn / info), category, verbatim excerpt
from the draft, and a one-line fix suggestion.

Hard rule: the LLM verifier prompt instructs Haiku to **only** flag
factual disagreements with the provided ground truth — not stylistic
preferences, not "this could be improved". A noisy verifier trains the
specialist to ignore it; we'd rather under-flag than over-flag.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, cast

import anthropic

from .draft import fmt_pl_datetime
from .models import (
    DraftBundle,
    Finding,
    FirmProfile,
    SiwzRequirements,
    TenderAnnouncement,
    VerificationReport,
)


# Mirror draft.py / siwz_extract.py pricing — keep formulas identical.
PRICING_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5":  (1.00,  5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-7":  (15.00, 75.00),
}

DEFAULT_MODEL = os.environ.get("TENDER_AGENT_VERIFY_MODEL", "claude-haiku-4-5")
# 8192 (vs. an earlier 4096) — Sonnet-quality drafts produce 8-12 finding
# objects easily, and 4096 was truncating the JSON tool-input mid-string.
MAX_OUTPUT_TOKENS = 8192


# ---------------------------------------------------------------------------
# Deterministic checks
# ---------------------------------------------------------------------------

# Map of Polish diacritics → ASCII equivalents. Used to detect drafts
# that strip a single accent — common LLM failure mode on long Polish
# proper names.
_DIACRITIC_MAP = {
    "ą": "a", "Ą": "A",
    "ć": "c", "Ć": "C",
    "ę": "e", "Ę": "E",
    "ł": "l", "Ł": "L",
    "ń": "n", "Ń": "N",
    "ó": "o", "Ó": "O",
    "ś": "s", "Ś": "S",
    "ź": "z", "Ź": "Z",
    "ż": "z", "Ż": "Z",
}


def _strip_diacritic_variants(target: str) -> list[str]:
    """Generate variants of `target` with one diacritic stripped to ASCII.

    For each diacritic in the target, return a string with that one
    occurrence demoted. Catches single-accent-drop typos without flagging
    full transliterations (which the LLM check would catch anyway).
    """
    variants: list[str] = []
    for i, ch in enumerate(target):
        if ch in _DIACRITIC_MAP:
            variants.append(target[:i] + _DIACRITIC_MAP[ch] + target[i + 1:])
    return variants


def _single_deletion_variants(target: str) -> list[str]:
    """Generate variants of `target` with exactly one character deleted.

    Skips very short targets (≤4 chars) to avoid false positives — short
    company tokens like `IBM` would generate `BM`/`IM`/`IB` which match
    anything.
    """
    if len(target) <= 4:
        return []
    return [target[:i] + target[i + 1:] for i in range(len(target))]


def _check_firm_identifiers(
    draft_text: str, firm: FirmProfile
) -> list[Finding]:
    """Deterministic firm-name + NIP + REGON checks.

    Looks for:
    - exact substring presence of firm.legal_name AND/OR firm.short_name
    - near-misses (single-char deletion, single-diacritic strip) of either
      name — these typically indicate an LLM dropped/replaced a letter
    - firm.nip / firm.regon / firm.krs must appear verbatim
    """
    findings: list[Finding] = []

    legal_present = firm.legal_name in draft_text
    short_present = firm.short_name in draft_text

    if not (legal_present or short_present):
        findings.append(Finding(
            severity="error",
            category="firm_name_typo",
            excerpt=f"(neither '{firm.legal_name}' nor '{firm.short_name}' appears in draft)",
            suggestion=(
                f"Insert the firm name verbatim — formal documents need "
                f"the legal name, signature blocks can use the short form."
            ),
            source="deterministic",
        ))

    # Near-miss check — only worth running when at least one form is
    # present (otherwise we'd just be re-flagging the absence above).
    for target in (firm.legal_name, firm.short_name):
        if target in draft_text:
            continue
        variants = _single_deletion_variants(target) + _strip_diacritic_variants(target)
        for variant in variants:
            if variant != target and variant in draft_text:
                findings.append(Finding(
                    severity="error",
                    category="firm_name_typo",
                    excerpt=variant,
                    suggestion=(
                        f"Found '{variant}' in draft — expected '{target}'. "
                        f"Single-char drop or diacritic strip; fix verbatim."
                    ),
                    source="deterministic",
                ))
                break  # one report per missing target is enough

    # NIP / REGON / KRS verbatim presence.
    if firm.nip not in draft_text:
        findings.append(Finding(
            severity="error",
            category="missing_field",
            excerpt=f"(firm NIP '{firm.nip}' absent from draft)",
            suggestion=f"NIP must be cited as {firm.nip!r} in Section A's identification block.",
            source="deterministic",
        ))
    if firm.regon and firm.regon not in draft_text:
        # REGON is less universally required than NIP, so a `warn`.
        findings.append(Finding(
            severity="warn",
            category="missing_field",
            excerpt=f"(firm REGON '{firm.regon}' absent from draft)",
            suggestion=(
                f"Firm profile has REGON {firm.regon} — drafts typically "
                f"cite it alongside NIP in the bid letter header."
            ),
            source="deterministic",
        ))

    return findings


def _check_announcement_identifiers(
    draft_text: str, ann: TenderAnnouncement
) -> list[Finding]:
    """Deterministic checks for announcement identifiers + organization data."""
    findings: list[Finding] = []

    # BZP number — must appear with the slash/space form exactly.
    if ann.bzp_number not in draft_text:
        findings.append(Finding(
            severity="error",
            category="wrong_identifier",
            excerpt=f"(BZP number {ann.bzp_number!r} not found in draft)",
            suggestion=(
                f"Every draft must cite the BZP number verbatim — "
                f"`{ann.bzp_number}` — at least once."
            ),
            source="deterministic",
        ))

    # Organization name — case-sensitive verbatim. Polish public sector
    # entities frequently uppercase their full names; the drafter must
    # preserve casing.
    if ann.organization_name not in draft_text:
        findings.append(Finding(
            severity="warn",
            category="missing_field",
            excerpt=f"(contracting authority '{ann.organization_name}' not found verbatim)",
            suggestion=(
                "Contracting-authority name should appear verbatim in "
                "Section A or B; preserve original casing."
            ),
            source="deterministic",
        ))

    # Authority NIP must be cited verbatim in the JEDZ block.
    if ann.organization_nip not in draft_text:
        findings.append(Finding(
            severity="error",
            category="wrong_identifier",
            excerpt=f"(authority NIP {ann.organization_nip!r} absent)",
            suggestion=(
                f"Authority NIP {ann.organization_nip} must be cited in the "
                "JEDZ Section B identification block."
            ),
            source="deterministic",
        ))

    if ann.organization_regon and ann.organization_regon not in draft_text:
        findings.append(Finding(
            severity="warn",
            category="missing_field",
            excerpt=f"(authority REGON '{ann.organization_regon}' absent)",
            suggestion=(
                f"Authority REGON {ann.organization_regon} is in the source "
                "announcement — JEDZ Section B typically cites it."
            ),
            source="deterministic",
        ))

    return findings


def _nip_strip(nip: str) -> str:
    """Normalize a NIP to digits-only for set membership."""
    return re.sub(r"\D", "", nip)


def _check_no_unknown_nips(
    draft_text: str, firm: FirmProfile, ann: TenderAnnouncement
) -> list[Finding]:
    """Scan for any 10-digit token in the draft that isn't a known NIP.

    A 10-digit run not matching either firm.nip or ann.organization_nip
    is suspicious — likely a hallucinated identifier. Phones (typically
    9 digits + country code, formatted with spaces) won't trip this.
    """
    findings: list[Finding] = []
    expected = {_nip_strip(firm.nip), _nip_strip(ann.organization_nip)}
    # Some firm KRS values are exactly 10 digits (zero-padded), which
    # would otherwise look identical to a NIP and be flagged. Accept
    # known KRS values; same for any 10-digit REGON variant.
    if firm.krs and len(_nip_strip(firm.krs)) == 10:
        expected.add(_nip_strip(firm.krs))
    if firm.regon and len(_nip_strip(firm.regon)) == 10:
        expected.add(_nip_strip(firm.regon))

    # Match 10-digit runs not adjacent to other digits (word boundary).
    # `\b\d{10}\b` is the cleanest expression.
    for match in re.finditer(r"\b\d{10}\b", draft_text):
        token = match.group(0)
        if token not in expected:
            # Snippet around the token for excerpt — ±40 chars, trimmed.
            start = max(0, match.start() - 40)
            end = min(len(draft_text), match.end() + 40)
            snippet = draft_text[start:end].replace("\n", " ").strip()
            findings.append(Finding(
                severity="error",
                category="wrong_identifier",
                excerpt=f"…{snippet}…",
                suggestion=(
                    f"10-digit token {token!r} doesn't match firm NIP ({firm.nip}) "
                    f"or authority NIP ({ann.organization_nip}) — likely fabricated."
                ),
                source="deterministic",
            ))

    return findings


def run_deterministic_checks(
    draft_text: str,
    firm: FirmProfile,
    ann: TenderAnnouncement,
) -> list[Finding]:
    """Run all string/regex checks. Pure, no network."""
    findings: list[Finding] = []
    findings.extend(_check_firm_identifiers(draft_text, firm))
    findings.extend(_check_announcement_identifiers(draft_text, ann))
    findings.extend(_check_no_unknown_nips(draft_text, firm, ann))
    return findings


# ---------------------------------------------------------------------------
# LLM cross-check (SIWZ trace + factual consistency)
# ---------------------------------------------------------------------------

LLM_SYSTEM_PROMPT = """Jesteś asystentem polskiego specjalisty od zamówień publicznych. Twoja praca to **weryfikacja szkicu oferty** pod kątem zgodności faktograficznej z dostępnym źródłem prawdy.

Dostajesz trzy rzeczy:
1. **Szkic oferty** w formacie Markdown (sekcje A-D).
2. **Strukturalne dane ogłoszenia BZP** (TenderAnnouncement).
3. **Strukturalne wymagania SIWZ** (SiwzRequirements) — TYLKO gdy SIWZ była zaczytana. Gdy SIWZ brak, ta sekcja jest oznaczona „BRAK SIWZ".

**Twoje zadanie:** zidentyfikować każdy fakt cytowany w szkicu, którego **nie da się odnaleźć** w danych źródłowych. Skupiasz się na:

- **Cytaty SIWZ w Sekcji D** — kary umowne, kwoty wadium, terminy, kryteria oceny, wymagane dokumenty, warunki udziału. Jeżeli szkic mówi „wadium = 5 000 PLN" a `SiwzRequirements.wadium` jest puste / null → zgłoś `fabricated_siwz_citation`.
- **Konkretne liczby i daty** w Sekcjach A, B, C — terminy składania ofert, terminy realizacji, okresy związania ofertą, wagi kryteriów. Każda z tych wartości musi znaleźć odzwierciedlenie w danych źródłowych.
- **Nazwy własne** — instytucji zamawiającej, miasta, kodów CPV. Drobne literówki / przekręcenia raportuj jako `inconsistency`.

**Czego NIE zgłaszasz** (twarda zasada — łamanie którejkolwiek z poniższych = noise, który specjalista zignoruje):
- preferencji stylistycznych („to zdanie powinno być krótsze", „spójność formatu dat")
- ogólnych uwag co do struktury („dodaj nagłówek")
- **pól oznaczonych w szkicu jako `[DO UZUPEŁNIENIA: ...]`** — to *celowa* luka, nie błąd. **Zignoruj je całkowicie**, niezależnie od tego, jak istotne się wydają. Drafter sygnalizuje gap, specjalista go wypełni.
- spraw, gdzie szkic mówi „specjalista musi zweryfikować" lub „w wyciągu SIWZ nie podano" — drafter prawidłowo sygnalizuje gap
- brakujących detali, których SIWZ nie precyzowała (np. drafter pomija specjalistyczne wymagania techniczne, których SiwzRequirements.additional_notes nie zawiera)
- statusu MŚP wykonawcy, podwykonawców, polegania na zasobach — te pola JEDZ Część II celowo zostawia się specjaliście, drafter wstawia tam `[DO UZUPEŁNIENIA]`

**Severity:**
- `error` — fakt sprzeczny z danymi źródłowymi (wymyślona kwota wadium, zły NIP, niezgodna data)
- `warn` — fakt podejrzany, którego nie można jednoznacznie zweryfikować
- `info` — uwaga drobna, nieobowiązkowa do poprawienia

**Format wyjściowy:** wywołanie narzędzia `record_findings` z listą obiektów `{severity, category, excerpt, suggestion}`. Maksymalnie 15 findings. Jeżeli wszystko OK — wywołaj narzędzie z pustą listą `findings: []`."""


SYSTEM_BLOCK = [
    {
        "type": "text",
        "text": LLM_SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }
]


VERIFY_TOOL: dict[str, Any] = {
    "name": "record_findings",
    "description": (
        "Zapisz listę zidentyfikowanych problemów w szkicu oferty. Wywołaj "
        "narzędzie raz, z pełną listą findings (może być pusta jeżeli OK)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "maxItems": 15,
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": {
                            "type": "string",
                            "enum": ["error", "warn", "info"],
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "fabricated_siwz_citation",
                                "wrong_value",
                                "inconsistency",
                                "missing_field",
                                "other",
                            ],
                        },
                        "excerpt": {
                            "type": "string",
                            "description": (
                                "Verbatim fragment from the draft (preferred) "
                                "or short description if structural."
                            ),
                        },
                        "suggestion": {
                            "type": "string",
                            "description": "One-line concrete fix for the specialist.",
                        },
                    },
                    "required": ["severity", "category", "excerpt", "suggestion"],
                },
            },
        },
        "required": ["findings"],
    },
}


def _announcement_summary(ann: TenderAnnouncement) -> str:
    """Compact source-of-truth dump for the verifier prompt.

    Times are rendered in Polish wall-clock (Europe/Warsaw), matching
    what the drafter and CLI display. Feeding the LLM raw UTC ISO here
    caused a false-positive feedback loop: drafts show 09:00 local,
    verifier compared against 07:00 UTC and flagged the correct value
    as wrong.
    """
    bits = [
        f"Numer BZP: {ann.bzp_number}",
        f"Identyfikator OCDS: {ann.tender_id_ocds}",
        f"Data publikacji: {ann.publication_date.date().isoformat()}",
        f"Zamawiający: {ann.organization_name}",
        f"  NIP: {ann.organization_nip}",
    ]
    if ann.organization_regon:
        bits.append(f"  REGON: {ann.organization_regon}")
    if ann.submitting_offers_date:
        bits.append(
            f"Termin składania ofert (czas polski): {fmt_pl_datetime(ann.submitting_offers_date)}"
        )
    bits.append(f"Tytuł: {ann.order_object}")
    if ann.cpv_main:
        bits.append(f"Główny CPV: {ann.cpv_main.code} — {ann.cpv_main.label}")
    if ann.realization_period:
        bits.append(f"Okres realizacji (z ogłoszenia): {ann.realization_period}")
    if ann.criteria:
        bits.append("Kryteria (z ogłoszenia):")
        for c in ann.criteria:
            bits.append(f"  - {c.name}: {c.weight_pct:.0f}%")
    return "\n".join(bits)


def _siwz_summary(siwz: SiwzRequirements) -> str:
    """Compact source-of-truth dump for SiwzRequirements."""
    return siwz.model_dump_json(indent=2, exclude={"source_pdf_path", "extraction_model"})


def _user_prompt(
    draft_text: str,
    ann: TenderAnnouncement,
    siwz: SiwzRequirements | None,
) -> str:
    siwz_block = (
        _siwz_summary(siwz) if siwz is not None
        else "BRAK SIWZ — szkic generowany bez kontekstu SIWZ. Weryfikuj wyłącznie zgodność z TenderAnnouncement (sekcje A, B, C)."
    )
    return f"""# Szkic do weryfikacji

{draft_text}

---

# Dane źródłowe — TenderAnnouncement

{_announcement_summary(ann)}

---

# Dane źródłowe — SiwzRequirements

{siwz_block}

---

# Twoje zadanie

Przejdź szkic sekcja po sekcji. Wywołaj `record_findings` z listą problemów. Pamiętaj: tylko sprzeczności faktograficzne, nie stylistyka."""


def _estimate_cost_usd(
    model: str,
    input_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
    output_tokens: int,
) -> float:
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
    bzp_number: str,
    model: str,
    input_tokens: int,
    cache_creation: int,
    cache_read: int,
    output_tokens: int,
    wall_seconds: float,
    cost_usd: float,
    n_findings: int,
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{datetime.now(timezone.utc).date().isoformat()}.jsonl"
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": "verify",
        "bzp": bzp_number,
        "model": model,
        "wall_s": round(wall_seconds, 2),
        "input_tokens": input_tokens,
        "cache_creation_tokens": cache_creation,
        "cache_read_tokens": cache_read,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd, 5),
        "n_findings": n_findings,
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _resolve_api_key(api_key: Optional[str]) -> Optional[str]:
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


def run_llm_check(
    draft_text: str,
    ann: TenderAnnouncement,
    siwz: SiwzRequirements | None,
    *,
    bzp_number: str,
    model: str = DEFAULT_MODEL,
    log_dir: Path | None = None,
    api_key: Optional[str] = None,
) -> tuple[list[Finding], float]:
    """Run the LLM cross-check. Returns (findings, cost_usd).

    Skips silently when API key is missing — caller should treat this
    as "deterministic-only" mode (still useful for the typo class).
    """
    resolved_key = _resolve_api_key(api_key)
    if not resolved_key:
        return ([], 0.0)

    client = anthropic.Anthropic(api_key=resolved_key)
    start = time.time()
    response = client.messages.create(  # type: ignore[call-overload]
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM_BLOCK,
        tools=[VERIFY_TOOL],
        tool_choice={"type": "tool", "name": VERIFY_TOOL["name"]},
        messages=[{"role": "user", "content": _user_prompt(draft_text, ann, siwz)}],
    )
    wall = time.time() - start

    tool_input: dict[str, Any] | None = None
    for block in response.content:
        if isinstance(block, anthropic.types.ToolUseBlock) and block.name == VERIFY_TOOL["name"]:
            tool_input = cast(dict[str, Any], block.input)
            break

    if tool_input is None:
        text_blocks = "".join(
            block.text for block in response.content
            if isinstance(block, anthropic.types.TextBlock)
        )
        raise ValueError(
            f"Verifier model {model} did not call record_findings. "
            f"Text fallback head: {text_blocks[:300]!r}"
        )

    # Haiku occasionally violates the schema and emits `findings` as a
    # JSON-string instead of an array, or returns array items as strings.
    # Be defensive without losing data: try to recover JSON, then accept
    # dict-items and coerce string-items into an `info`-severity stub.
    raw_findings_any: Any = tool_input.get("findings", [])
    if isinstance(raw_findings_any, str):
        try:
            raw_findings_any = json.loads(raw_findings_any)
        except json.JSONDecodeError:
            raw_findings_any = [raw_findings_any]
    if not isinstance(raw_findings_any, list):
        raw_findings_any = [raw_findings_any]

    findings: list[Finding] = []
    for item in raw_findings_any:
        if isinstance(item, dict):
            findings.append(Finding(**{**item, "source": "llm"}))
        elif isinstance(item, str) and item.strip():
            findings.append(Finding(
                severity="info",
                category="other",
                excerpt=item.strip()[:300],
                suggestion="(model returned a plain string instead of a structured finding)",
                source="llm",
            ))

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

    if log_dir is not None:
        _log_call(
            log_dir=log_dir,
            bzp_number=bzp_number,
            model=model,
            input_tokens=usage.input_tokens,
            cache_creation=cache_creation,
            cache_read=cache_read,
            output_tokens=usage.output_tokens,
            wall_seconds=wall,
            cost_usd=cost,
            n_findings=len(findings),
        )

    return (findings, cost)


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def verify_draft(
    *,
    draft_path: Path,
    bundle: DraftBundle | None,
    ann: TenderAnnouncement,
    firm: FirmProfile,
    siwz: SiwzRequirements | None = None,
    model: str = DEFAULT_MODEL,
    log_dir: Path | None = None,
    api_key: Optional[str] = None,
    skip_llm: bool = False,
) -> VerificationReport:
    """Full verification pipeline.

    `bundle` is optional — when None, the function reads `draft_path` and
    verifies the rendered Markdown directly. Callers that have just
    generated a `DraftBundle` should pass it to avoid the redundant read.

    `skip_llm=True` runs only deterministic checks. Useful for fast
    pre-flight checks where the typo class is the main concern.
    """
    if bundle is not None:
        # Join ALL seven sections (A-G). JEDZ Part II (sekcja E) is where
        # firm NIP/REGON/KRS live in a Phase 0.8+ draft; if we only join
        # A-D the deterministic check falsely reports those identifiers
        # missing and the retry loop never converges.
        draft_text = "\n\n".join([
            bundle.oswiadczenie_wykluczenie_md,
            bundle.jedz_czesc_1_md,
            bundle.list_intencyjny_md,
            bundle.model_notes or "",
            bundle.jedz_czesc_2_md or "",
            bundle.jedz_czesc_3_md or "",
            bundle.jedz_czesc_4_md or "",
        ])
    else:
        draft_text = draft_path.read_text(encoding="utf-8")

    findings = run_deterministic_checks(draft_text, firm, ann)

    llm_cost = 0.0
    llm_model: Optional[str] = None
    if not skip_llm:
        llm_findings, llm_cost = run_llm_check(
            draft_text,
            ann,
            siwz,
            bzp_number=ann.bzp_number,
            model=model,
            log_dir=log_dir,
            api_key=api_key,
        )
        findings.extend(llm_findings)
        llm_model = model

    passed = not any(f.severity == "error" for f in findings)

    return VerificationReport(
        findings=findings,
        passed=passed,
        draft_path=str(draft_path.resolve()),
        bzp_number=ann.bzp_number,
        llm_model=llm_model,
        llm_cost_usd=round(llm_cost, 5),
    )


# ---------------------------------------------------------------------------
# CLI (standalone — also wired into tender_agent.cli)
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tender_agent.verify",
        description="Verify a draft against firm + announcement + (optional) SIWZ.",
    )
    p.add_argument("draft_path", help="Path to the draft Markdown file.")
    p.add_argument("--firm", required=True, help="Path to FirmProfile JSON.")
    p.add_argument("--announcement", required=True, help="Path to TenderAnnouncement JSON.")
    p.add_argument("--siwz", default=None, help="Path to SiwzRequirements JSON (optional).")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument(
        "--skip-llm",
        action="store_true",
        help="Run deterministic checks only (no Anthropic API call).",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Where to write the VerificationReport JSON. Default: <draft>.verify.json.",
    )
    p.add_argument(
        "--log-dir",
        default=None,
        help=(
            "Directory for JSONL cost log. Default: "
            "<repo>/tender_agent/_logs/."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    draft_path = Path(args.draft_path).resolve()
    firm = FirmProfile.model_validate_json(Path(args.firm).read_text(encoding="utf-8"))
    ann = TenderAnnouncement.model_validate_json(
        Path(args.announcement).read_text(encoding="utf-8")
    )
    siwz: SiwzRequirements | None = None
    if args.siwz:
        siwz = SiwzRequirements.model_validate_json(
            Path(args.siwz).read_text(encoding="utf-8")
        )

    if args.log_dir:
        log_dir: Path | None = Path(args.log_dir)
    else:
        log_dir = Path(__file__).resolve().parents[3] / "tender_agent" / "_logs"

    report = verify_draft(
        draft_path=draft_path,
        bundle=None,
        ann=ann,
        firm=firm,
        siwz=siwz,
        model=args.model,
        log_dir=log_dir,
        skip_llm=args.skip_llm,
    )

    out_path = Path(args.out) if args.out else draft_path.with_suffix(".verify.json")
    out_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    status = "PASS" if report.passed else "FAIL"
    error_count = sum(1 for f in report.findings if f.severity == "error")
    warn_count = sum(1 for f in report.findings if f.severity == "warn")
    info_count = sum(1 for f in report.findings if f.severity == "info")
    print(
        f"{status}  errors={error_count} warns={warn_count} infos={info_count} "
        f"cost=${report.llm_cost_usd:.4f}  → {out_path}"
    )
    for f in report.findings:
        marker = {"error": "✗", "warn": "!", "info": "·"}.get(f.severity, "?")
        excerpt = f.excerpt if len(f.excerpt) <= 100 else f.excerpt[:97] + "..."
        print(f"  {marker} [{f.severity}/{f.category}/{f.source}] {excerpt}")
        print(f"      → {f.suggestion}")

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())

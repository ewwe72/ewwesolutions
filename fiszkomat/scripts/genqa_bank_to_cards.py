"""Convert the FULL question bank into mikrobio.cards.json (MCQ format).

Operator: "to daj cały ten bank" — every usable bank question lands on
the site as a flashcard. Existing curated cards (with agent-written
explanations in m/i/c) get ENRICHED by merging their content into the
matching bank card; the rest are bare bank Q+A with a Murray pointer.

Future plan (separate milestone): generate NEW questions from Murray
that AVOID overlapping with the bank, for the next zaliczenie cohort.
This script just dumps the bank.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from fiszkomat.core import Card, validate_cards, pack_apkg

WORK = Path(__file__).resolve().parent.parent / "_work"
OUT_DIR = Path(__file__).resolve().parent.parent / "test_docs" / "out"
BANK_JSON = WORK / "bank_parsed.json"
CATALOG_JSON = WORK / "catalog.json"
EXISTING_CARDS = OUT_DIR / "mikrobio.cards.json"
OUT_CARDS = OUT_DIR / "mikrobio.cards.json"
OUT_APKG = OUT_DIR / "mikrobio.apkg"


def normalize(s: str) -> str:
    """Lowercase + strip punctuation for stem matching."""
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_usable(q: dict) -> bool:
    """Question quality filter — relaxed 2026-05-18 after operator+Paulina
    feedback ("lepiej niekompletne pytanie niż żadne"). Cards with no
    correct marker OR multiple correct markers are now KEPT — the
    reviewer flags them as "Odpowiedź: nieoznaczona w bazie".

    Hard rejects only for parser-noise patterns (stems/options too
    short, way out of range option count).
    """
    stem = (q.get("stem") or "").strip()
    if len(stem) < 30:
        return False   # cross-page split or extraction artifact
    if len(stem) > 500:
        return False   # likely matching-question that won't render cleanly
    options = q.get("options") or []
    if not (2 <= len(options) <= 8):
        return False   # outside this range = parser miscount
    for o in options:
        body = (o.get("text") or "").strip()
        if len(body) < 3:
            return False  # 1-2 char options = extraction noise
        if len(body) > 300:
            return False
    return True


def topic_to_chapter(topic: str, topic_chapter_map: dict[str, list[dict]]) -> dict | None:
    """Pick the first Murray chapter mapped to this topic — used for source cit."""
    chapters = topic_chapter_map.get(topic) or []
    return chapters[0] if chapters else None


def build_base_card(q: dict, topic_chapter_map: dict[str, list[dict]]) -> dict | None:
    """Bank question → minimal MCQ card record (dict, ready for Card()).

    Single-correct: normal MCQ with t=correct option text, correct_letter set.
    Zero or multi-correct: ambiguous card with empty correct_letter; reviewer
    flags as "Odpowiedź: nieoznaczona w bazie".
    """
    correct_marked = [o for o in q["options"] if o.get("correct")]
    if len(correct_marked) == 1:
        correct_opt = correct_marked[0]
        t_text = (correct_opt.get("text") or "").strip()
        correct_letter = correct_opt["letter"]
    else:
        # 0 or multi-correct — bank parser couldn't disambiguate
        correct_opt = None
        t_text = ""
        correct_letter = ""
    chapter = topic_to_chapter(q["topic"], topic_chapter_map)
    chapter_num = chapter["num"] if chapter else 0
    chapter_pages = (
        f"s. {chapter['page_start']}–{chapter['page_end']}"
        if chapter else "—"
    )

    # Build the n field: source pointer + Murray + optional bank annotations
    n_parts = []
    n_parts.append(f"Pytanie z bazy egzaminacyjnej (s. {q['page']}).")
    if chapter:
        n_parts.append(f"Murray, rozdz. {chapter['num']}, {chapter_pages} ({chapter['title']}).")
    # Bank annotations — red text near correct option = WHY this is right
    correct_ann = (correct_opt.get("annotation") or "").strip() if correct_opt else ""
    raw_red = (q.get("raw_red_around") or "").strip()
    if correct_ann and len(correct_ann) >= 8:
        n_parts.append(f"Notka z bazy: {correct_ann}")
    elif raw_red and len(raw_red) >= 8:
        n_parts.append(f"Adnotacje z bazy: {raw_red[:240]}")
    # For ambiguous cards (no correct marker), tell the student
    if not correct_opt:
        n_parts.append(
            "Baza nie oznaczyła jednoznacznie poprawnej odpowiedzi (pytanie zachowane — sprawdź w Murrayu)."
        )
    n_text = " ".join(n_parts)[:790]

    # t (answer headline) capped
    if len(t_text) > 195:
        t_text = t_text[:190] + "…"

    return {
        "z": chapter_num if 0 < chapter_num <= 99 else 0,
        "t": t_text,
        "d": q["stem"].strip(),
        "options": [(o.get("text") or "").strip() for o in q["options"]],
        "correct_letter": correct_letter,
        "m": "",
        "i": "",
        "c": "",
        "n": n_text,
    }


def load_curated() -> list[dict]:
    """Load whatever's currently in mikrobio.cards.json (the 78 agent cards)
    so we can merge their m/i/c/n into matching bank cards."""
    if not EXISTING_CARDS.exists():
        return []
    return json.loads(EXISTING_CARDS.read_text(encoding="utf-8"))


def enrich_with_curated(base_cards: list[dict], curated: list[dict]) -> int:
    """For each base bank card, if a curated card has a near-identical stem,
    copy its m/i/c (and merge its n) into the base card. Mutates in place.
    Returns count of enriched cards."""
    if not curated:
        return 0
    # Pre-normalize all curated stems for fast lookup
    norm_curated = [(normalize(c.get("d", "")), c) for c in curated if c.get("d")]
    enriched = 0
    for base in base_cards:
        base_norm = normalize(base["d"])
        best_ratio = 0.0
        best_curated = None
        for cnorm, cobj in norm_curated:
            # Quick filter: first 30 chars must overlap
            if len(cnorm) < 20 or len(base_norm) < 20:
                continue
            if cnorm[:20] not in base_norm and base_norm[:20] not in cnorm:
                continue
            r = SequenceMatcher(None, base_norm, cnorm).ratio()
            if r > best_ratio:
                best_ratio = r
                best_curated = cobj
        if best_ratio >= 0.85 and best_curated is not None:
            # Merge — take curated's m/i/c (they're hand-written explanations);
            # merge n by appending curated's n (Murray + agent notes) AFTER
            # the base's n (which is bank + auto-Murray pointer), deduped.
            base["m"] = (best_curated.get("m") or "").strip()
            base["i"] = (best_curated.get("i") or "").strip()
            base["c"] = (best_curated.get("c") or "").strip()
            curated_n = (best_curated.get("n") or "").strip()
            if curated_n and curated_n not in base["n"]:
                merged_n = base["n"] + " — " + curated_n
                base["n"] = merged_n[:790]
            # If curated has its own correct_letter and they differ — keep base's
            # (bank is canonical). If curated had a better t, keep curated's.
            if best_curated.get("t"):
                base["t"] = best_curated["t"][:195]
            enriched += 1
    return enriched


def main():
    bank = json.loads(BANK_JSON.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
    # catalog.json shape: { "catalog": [ {topic, murray_chapters: [...]}, ...] }
    topic_chapter_map = {
        entry["topic"]: entry["murray_chapters"]
        for entry in catalog["catalog"]
    }

    bank_qs = bank["questions"]
    print(f"bank: {len(bank_qs)} questions")
    usable = [q for q in bank_qs if is_usable(q)]
    print(f"usable after filter: {len(usable)}")

    base_cards = []
    for q in usable:
        rec = build_base_card(q, topic_chapter_map)
        if rec is not None:
            base_cards.append(rec)
    print(f"base cards built: {len(base_cards)}")

    curated = load_curated()
    print(f"existing curated cards on disk: {len(curated)}")
    enriched = enrich_with_curated(base_cards, curated)
    print(f"enriched with curated content: {enriched} cards")

    # Validate (also runs sha1 dedup on (t,d))
    valid, rejected = validate_cards(base_cards)
    print(f"validate_cards: {len(valid)} valid, {len(rejected)} rejected")
    if rejected:
        # Show reason histogram
        reasons: dict[str, int] = {}
        for _, reason in rejected:
            reasons[reason] = reasons.get(reason, 0) + 1
        for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"  {c:>4}  {r}")

    # Write outputs
    OUT_CARDS.write_text(
        json.dumps([c.model_dump() for c in valid], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {OUT_CARDS} ({len(valid)} cards, {OUT_CARDS.stat().st_size / 1024:.0f} KB)")

    pack_apkg(valid, deck_name="fiszkomat — Mikrobiologia (Baza+Książka)", out_path=OUT_APKG)
    print(f"wrote {OUT_APKG} ({OUT_APKG.stat().st_size / 1024:.0f} KB)")

    # Summary stats
    with_explanation = sum(1 for c in valid if c.m or c.i or c.c)
    with_bank_annotation = sum(1 for c in valid if "Notka z bazy" in c.n or "Adnotacje z bazy" in c.n)
    by_topic = {}
    by_chapter = {}
    for c in valid:
        # topic implicit via chapter z; track chapter
        by_chapter[c.z] = by_chapter.get(c.z, 0) + 1
    print()
    print(f"=== summary ===")
    print(f"  total valid cards:                  {len(valid)}")
    print(f"  with curated m/i/c explanation:     {with_explanation}")
    print(f"  with bank student-annotation:       {with_bank_annotation}")
    print(f"  cards per Murray chapter (top 10):")
    for ch, ct in sorted(by_chapter.items(), key=lambda x: -x[1])[:10]:
        print(f"    rozdz. {ch:>2}: {ct} cards")


if __name__ == "__main__":
    main()

"""Merge agent enrichment outputs (enrich_E{1-8}.json) back into
mikrobio.cards.json. Each agent emitted records keyed by card_index;
we update m/i/c in place, preserving everything else.

Run after any subset of agents complete — re-runnable; subsequent
runs will overwrite m/i/c from the latest enrichment files.
"""
from __future__ import annotations

import json
from pathlib import Path

from fiszkomat.core import Card, validate_cards, pack_apkg

WORK = Path(__file__).resolve().parent.parent / "_work"
OUT_DIR = Path(__file__).resolve().parent.parent / "test_docs" / "out"
CARDS_JSON = OUT_DIR / "mikrobio.cards.json"
APKG_OUT = OUT_DIR / "mikrobio.apkg"


def main():
    cards = json.loads(CARDS_JSON.read_text(encoding="utf-8"))
    total_before = sum(1 for c in cards if c["m"] or c["i"] or c["c"])
    print(f"cards already with m/i/c before merge: {total_before}/{len(cards)}")

    merged = 0
    by_file = {}
    # Discover all enrich_*.json files (round 1: enrich_E{N}.json,
    # round 2: enrich_E{N}_r2.json, tail batches: enrich_E_tail_r2.json, etc.).
    enrich_paths = sorted(WORK.glob("enrich_E*.json"))
    for path in enrich_paths:
        tag = path.stem.replace("enrich_", "")
        if not path.exists():
            continue
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  {tag}: PARSE FAIL {e}")
            continue
        ok = 0
        for e in entries:
            idx = e.get("card_index")
            if idx is None or idx < 0 or idx >= len(cards):
                continue
            # Sanity: stem/answer should still match (no card index drift)
            if cards[idx].get("d") != e.get("d") or cards[idx].get("t") != e.get("t"):
                # Could be a stale index from a prior run; skip silently
                continue
            # Don't overwrite already-curated content
            existing = cards[idx]
            if existing["m"] or existing["i"] or existing["c"]:
                continue
            # Apply enrichment
            cards[idx]["m"] = (e.get("m") or "")[:800]
            cards[idx]["i"] = (e.get("i") or "")[:600]
            cards[idx]["c"] = (e.get("c") or "")[:600]
            ok += 1
            merged += 1
        by_file[tag] = ok
        print(f"  {tag}: applied {ok} of {len(entries)}")

    total_after = sum(1 for c in cards if c["m"] or c["i"] or c["c"])
    still_empty = sum(1 for c in cards if not (c["m"] or c["i"] or c["c"]))
    print()
    print(f"=== merge summary ===")
    print(f"  enrichment files seen:        {len(by_file)}")
    print(f"  cards applied this run:       {merged}")
    print(f"  cards with m/i/c after merge: {total_after}/{len(cards)}")
    print(f"  cards still empty:            {still_empty}")

    # Validate everything
    valid, rejected = validate_cards(cards)
    print(f"  validate_cards: {len(valid)} valid, {len(rejected)} rejected")
    if rejected:
        for raw, reason in rejected[:10]:
            print(f"    REJECTED [{reason}]: t={(raw.get('t') or '')[:60]!r}")

    # Write back
    CARDS_JSON.write_text(
        json.dumps([c.model_dump() for c in valid], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  wrote {CARDS_JSON} ({CARDS_JSON.stat().st_size / 1024:.0f} KB)")

    # Repack apkg
    pack_apkg(valid, deck_name="fiszkomat — Mikrobiologia (Baza+Książka)", out_path=APKG_OUT)
    print(f"  wrote {APKG_OUT} ({APKG_OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()

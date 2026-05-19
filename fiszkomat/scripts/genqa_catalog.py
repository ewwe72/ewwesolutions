"""Topic catalog: cross-reference bank_parsed.json with textbook_parsed.json
to map every bank topic to candidate Murray chapters, with frequency stats.

Hardcoded mapping (manual after reviewing both indexes) — fuzzy-matching
the topic strings to chapter titles would be brittle (Polish synonyms,
abbreviations, mixed scoping). Manual is more accurate and easy to audit.

Output: _work/catalog.md (human-readable) + _work/catalog.json (machine-readable)
"""
from __future__ import annotations

import json
from pathlib import Path

WORK = Path(__file__).resolve().parent.parent / "_work"
BANK_JSON = WORK / "bank_parsed.json"
BOOK_JSON = WORK / "textbook_parsed.json"
OUT_MD = WORK / "catalog.md"
OUT_JSON = WORK / "catalog.json"


# Map each bank topic → list of Murray chapter numbers covering that topic.
# Built by hand after reading both indexes; agents can refine if a chapter
# is missing for a given topic.
TOPIC_TO_CHAPTERS: dict[str, list[int]] = {
    "BAKTERIE — PODSTAWY, PODŁOŻA, BARWIENIA, DIAGNOSTYKA": [4, 5, 6, 12, 13, 14, 15, 16],
    "DEZYNFEKCJA I STERYLIZACJA": [3],
    "PRĄTKI, LASECZKI, CORYNEBACTERIACEAE": [20, 21, 22, 30],
    "ZIARENKOWCE GRAM-DODATNIE I GRAM-UJEMNE": [18, 19, 23],
    "ANTYBIOTYKI": [17],
    "KRĘTKI I CHOROBY WENERYCZNE": [32, 35],
    "PAŁECZKI, KRĘTKI GRAM-UJEMNE": [24, 25, 27, 28, 29, 31, 33, 34],
    "WIRUSY — PODSTAWY, HODOWLE, DIAGNOSTYKA": [36, 37, 38, 39, 40],
    "WIRUSOLOGIA SZCZEGÓŁOWA": list(range(41, 56)),  # 41..55
    "GRZYBY": list(range(57, 68)),  # 57..67
    "INNE/MIX": [],  # cross-cutting, no fixed chapters
    "ZAKAŻENIA UKŁADU MOCZOWEGO": [25, 27],  # E.coli + Pseudomonas mostly
    "PARAZYTOLOGIA": list(range(68, 79)),  # 68..78
}


def main():
    bank = json.loads(BANK_JSON.read_text(encoding="utf-8"))
    book = json.loads(BOOK_JSON.read_text(encoding="utf-8"))

    # Build chapter index for quick lookup
    chap_by_num = {c["num"]: c for c in book["chapters"]}

    # Compute stats per topic
    catalog = []
    for topic_name, chapter_nums in TOPIC_TO_CHAPTERS.items():
        topic_questions = [q for q in bank["questions"] if q["topic"] == topic_name]
        topic_question_pages = sorted({q["page"] for q in topic_questions})
        topic_page_range = (
            (min(topic_question_pages), max(topic_question_pages))
            if topic_question_pages else (None, None)
        )
        # Quality slice: only questions with a marked correct answer + sane stem length
        usable = [
            q for q in topic_questions
            if any(o["correct"] for o in q["options"])
            and 20 <= len(q["stem"]) <= 400
            and 4 <= len(q["options"]) <= 6
        ]
        chapters_resolved = []
        for cn in chapter_nums:
            if cn in chap_by_num:
                c = chap_by_num[cn]
                chapters_resolved.append({
                    "num": cn,
                    "title": c["title"],
                    "page_start": c["page_start"],
                    "page_end": c["page_end"],
                })
        catalog.append({
            "topic": topic_name,
            "bank_question_count": len(topic_questions),
            "bank_usable_count": len(usable),
            "bank_page_range": topic_page_range,
            "with_annotations": sum(1 for q in topic_questions
                                    if q.get("raw_red_around") or any(o.get("annotation") for o in q["options"])),
            "murray_chapters": chapters_resolved,
            "murray_total_pages": sum(c["page_end"] - c["page_start"] + 1 for c in chapters_resolved),
        })

    # Write JSON
    OUT_JSON.write_text(json.dumps({"catalog": catalog}, ensure_ascii=False, indent=2), encoding="utf-8")

    # Write Markdown report
    lines = [
        "# Catalog — Mikrobiologia bank × Murray textbook",
        "",
        "Source files: `baza_mikrobiologia.pdf` (248p, 1105 questions extracted) ×",
        "`2018_murray_mikrobiologia.pdf` (924p, 77 chapters indexed).",
        "",
        "Topic mapping is hand-curated (see TOPIC_TO_CHAPTERS in genqa_catalog.py).",
        "",
        "## Summary by topic (sorted by question frequency)",
        "",
        "| Topic | Bank Qs | Usable | Page range | With annot. | Murray chapters | Murray pages |",
        "|---|---:|---:|---|---:|---|---:|",
    ]
    catalog_sorted = sorted(catalog, key=lambda x: -x["bank_question_count"])
    for c in catalog_sorted:
        pr = c["bank_page_range"]
        prr = f"{pr[0]}–{pr[1]}" if pr[0] else "—"
        chs = ", ".join(str(ch["num"]) for ch in c["murray_chapters"]) or "—"
        lines.append(
            f"| {c['topic'][:55]} | {c['bank_question_count']} | "
            f"{c['bank_usable_count']} | {prr} | {c['with_annotations']} | "
            f"{chs} | {c['murray_total_pages']} |"
        )
    lines.extend([
        "",
        "## Details per topic",
        "",
    ])
    for c in catalog_sorted:
        lines.append(f"### {c['topic']}")
        lines.append("")
        pr = c["bank_page_range"]
        prr = f"baza s.{pr[0]}–{pr[1]}" if pr[0] else "—"
        lines.append(f"- Bank: **{c['bank_question_count']} questions** "
                     f"({c['bank_usable_count']} usable), {prr}, "
                     f"{c['with_annotations']} with student annotations.")
        if c["murray_chapters"]:
            lines.append(f"- Murray chapters ({c['murray_total_pages']} pages total):")
            for ch in c["murray_chapters"]:
                lines.append(f"  - rozdz. {ch['num']:>2}  s.{ch['page_start']}–{ch['page_end']}  *{ch['title']}*")
        else:
            lines.append("- Murray chapters: none mapped (cross-cutting topic)")
        lines.append("")

    lines.append("## Suggested card-generation prioritization")
    lines.append("")
    lines.append("Ranked by `bank_usable_count` — these are the topics where we have")
    lines.append("the most clean source material for card generation:")
    lines.append("")
    for c in sorted(catalog, key=lambda x: -x["bank_usable_count"])[:10]:
        lines.append(f"1. **{c['topic']}** — {c['bank_usable_count']} usable Qs, "
                     f"{len(c['murray_chapters'])} Murray chapters covering "
                     f"{c['murray_total_pages']}p")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    # Console summary
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_MD}")
    print()
    print("=== topic catalog ===")
    for c in catalog_sorted:
        print(f"  {c['bank_question_count']:>4} bank Qs ({c['bank_usable_count']:>4} usable) "
              f"× {len(c['murray_chapters']):>2} Murray chapters → {c['topic']}")


if __name__ == "__main__":
    main()

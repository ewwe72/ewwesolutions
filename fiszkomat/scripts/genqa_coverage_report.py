"""Coverage report: for each of the 1105 bank questions, did it land in
mikrobio.cards.json? If not, why? Outputs a markdown report the operator
can scan for missed gems.

Reasons a bank question can be missing:
  - filter:no_correct       — bank parser didn't mark any option `correct`
  - filter:multi_correct    — more than one option marked correct (parser noise OR genuine "wybierz wszystkie" — not handled by single-correct MCQ schema)
  - filter:stem_too_short   — stem <30 chars (extraction artifact)
  - filter:stem_too_long    — stem >400 chars
  - filter:bad_option_count — <4 or >6 options
  - filter:option_too_short — at least one option <3 chars (extraction noise)
  - filter:option_too_long  — at least one option >300 chars
  - dedup                   — passed filters but (t,d) sha1 matches another card already in the deck — the report shows which winning card "absorbed" it
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

WORK = Path(__file__).resolve().parent.parent / "_work"
OUT_DIR = Path(__file__).resolve().parent.parent / "test_docs" / "out"
BANK_JSON = WORK / "bank_parsed.json"
CARDS_JSON = OUT_DIR / "mikrobio.cards.json"
OUT_MD = WORK / "coverage_report.md"


def is_usable_with_reason(q: dict) -> tuple[bool, str]:
    correct_letters = [o["letter"] for o in q.get("options", []) if o.get("correct")]
    if len(correct_letters) == 0:
        return False, "filter:no_correct"
    if len(correct_letters) > 1:
        return False, "filter:multi_correct"
    stem = (q.get("stem") or "").strip()
    if len(stem) < 30:
        return False, "filter:stem_too_short"
    if len(stem) > 400:
        return False, "filter:stem_too_long"
    options = q.get("options") or []
    if len(options) < 4 or len(options) > 6:
        return False, "filter:bad_option_count"
    for o in options:
        body = (o.get("text") or "").strip()
        if len(body) < 3:
            return False, "filter:option_too_short"
        if len(body) > 300:
            return False, "filter:option_too_long"
    return True, "usable"


def dedup_key(t: str, d: str) -> str:
    return hashlib.sha1((t.strip().lower() + "|" + d.strip().lower()).encode("utf-8")).hexdigest()


def main():
    bank = json.loads(BANK_JSON.read_text(encoding="utf-8"))
    cards = json.loads(CARDS_JSON.read_text(encoding="utf-8"))

    # Build sha1 lookup of cards on site: (t, d) -> card record
    site_by_key: dict[str, dict] = {}
    for c in cards:
        k = dedup_key(c.get("t", ""), c.get("d", ""))
        site_by_key[k] = c

    # Stats accumulators
    by_topic_total: dict[str, int] = {}
    by_topic_usable: dict[str, int] = {}
    by_topic_landed: dict[str, int] = {}
    skip_reasons: dict[str, int] = {}
    dedup_losers: list[dict] = []
    filtered_out: dict[str, list[dict]] = {}

    for q in bank["questions"]:
        topic = q.get("topic", "(unknown)")
        by_topic_total[topic] = by_topic_total.get(topic, 0) + 1
        ok, reason = is_usable_with_reason(q)
        if not ok:
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            filtered_out.setdefault(reason, []).append(q)
            continue
        by_topic_usable[topic] = by_topic_usable.get(topic, 0) + 1
        # Did it land? Build the same (t, d) the converter would have used
        correct_opt = next((o for o in q["options"] if o.get("correct")), None)
        if correct_opt is None:
            skip_reasons["unexpected:no_correct_after_filter"] = (
                skip_reasons.get("unexpected:no_correct_after_filter", 0) + 1
            )
            continue
        t = correct_opt.get("text", "").strip()
        if len(t) > 195:
            t = t[:190] + "…"
        d = q["stem"].strip()
        k = dedup_key(t, d)
        if k in site_by_key:
            by_topic_landed[topic] = by_topic_landed.get(topic, 0) + 1
        else:
            # Passed filters but didn't land — must be a dedup loser
            skip_reasons["dedup"] = skip_reasons.get("dedup", 0) + 1
            dedup_losers.append({
                "bank_page": q["page"],
                "topic": topic,
                "stem": d[:140],
                "correct_text": t[:80],
            })

    # Build report
    lines = []
    lines.append("# Coverage report — bank → site")
    lines.append("")
    lines.append(f"- Total bank questions parsed: **{len(bank['questions'])}**")
    lines.append(f"- Cards on site (after all filters + dedup): **{len(cards)}**")
    coverage = 100 * sum(by_topic_landed.values()) / max(sum(by_topic_total.values()), 1)
    lines.append(f"- Coverage (usable + landed): **{coverage:.1f}%** of total bank")
    lines.append("")
    lines.append("## Why bank questions are missing")
    lines.append("")
    lines.append("| Reason | Count | What it means |")
    lines.append("|---|---:|---|")
    descriptions = {
        "filter:no_correct": "bank parser found no option marked correct — usually a parsing artifact where the correct-answer bold formatting didn't trip the threshold",
        "filter:multi_correct": "more than one option marked correct — either bank parser noise OR a genuine \"wybierz wszystkie prawidłowe\" question that doesn't fit our single-correct MCQ schema",
        "filter:stem_too_short": "stem <30 chars — extraction artifact, usually a question that got split across pages",
        "filter:stem_too_long": "stem >400 chars — multi-paragraph stem (matching questions like \"Dopasuj X do Y\")",
        "filter:bad_option_count": "<4 or >6 options — usually parser failed to detect all options",
        "filter:option_too_short": "option <3 chars — usually a stray letter/number caught by parser",
        "filter:option_too_long": "option >300 chars — usually an option that absorbed a following question's text",
        "dedup": "passed all filters, but its (correct_answer, stem) sha1 matched another card already on the deck — semantically the same question",
        "unexpected:no_correct_after_filter": "internal: should not happen, indicates filter logic bug",
    }
    for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
        lines.append(f"| `{reason}` | {count} | {descriptions.get(reason, '(unknown)')} |")
    lines.append("")
    lines.append("## Coverage by topic")
    lines.append("")
    lines.append("| Topic | In bank | Usable | Landed | % of bank kept |")
    lines.append("|---|---:|---:|---:|---:|")
    for topic in sorted(by_topic_total.keys(), key=lambda t: -by_topic_total[t]):
        tot = by_topic_total[topic]
        usable = by_topic_usable.get(topic, 0)
        landed = by_topic_landed.get(topic, 0)
        pct = 100 * landed / max(tot, 1)
        lines.append(f"| {topic[:55]} | {tot} | {usable} | {landed} | {pct:.0f}% |")
    lines.append("")
    if dedup_losers:
        lines.append(f"## Dedup losers ({len(dedup_losers)})")
        lines.append("")
        lines.append("These bank questions passed all quality filters but didn't land because another bank question has the same (correct_answer, stem) sha1 — i.e. they're semantically identical. The deck has the canonical version; these are the rephrasings that got absorbed.")
        lines.append("")
        for d in dedup_losers[:30]:
            lines.append(f"- baza s.{d['bank_page']:>3} · *{d['topic'][:40]}* · answer=`{d['correct_text']}`")
            lines.append(f"  > {d['stem']}")
        if len(dedup_losers) > 30:
            lines.append(f"- ... and {len(dedup_losers) - 30} more (see full list in _work/coverage_dedup_losers.json)")
        # Full dump for completeness
        (WORK / "coverage_dedup_losers.json").write_text(
            json.dumps(dedup_losers, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines.append("")
    # Samples per filter reason
    lines.append("## Samples of filtered-out questions (max 5 per reason)")
    lines.append("")
    for reason in sorted(filtered_out.keys(), key=lambda r: -len(filtered_out[r])):
        items = filtered_out[reason]
        lines.append(f"### `{reason}` ({len(items)} total)")
        lines.append("")
        for q in items[:5]:
            stem = (q.get("stem") or "").strip()[:160]
            opt_count = len(q.get("options") or [])
            correct_count = sum(1 for o in q.get("options", []) if o.get("correct"))
            lines.append(f"- baza s.{q.get('page'):>3} · {opt_count} opt, {correct_count} correct · *{q.get('topic', '?')[:40]}*")
            lines.append(f"  > {stem!r}")
        if len(items) > 5:
            lines.append(f"- (+{len(items) - 5} more)")
        lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT_MD}")
    print()
    print("=== summary ===")
    print(f"  bank total:     {len(bank['questions'])}")
    print(f"  on site:        {len(cards)}")
    print(f"  coverage:       {coverage:.1f}%")
    print(f"  skip reasons:")
    for r, c in sorted(skip_reasons.items(), key=lambda x: -x[1]):
        print(f"    {c:>4}  {r}")


if __name__ == "__main__":
    main()

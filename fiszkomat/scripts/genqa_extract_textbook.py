"""Extract chapter index + per-page text from Murray's microbiology textbook
(Polish translation, 924 pages, scanned + OCR via Adobe Acrobat Pro DC).

OCR has artifacts (Greek letters mangled, table columns mashed). We don't try
to fix any of it — we just extract. The result is good enough for:
  - Building a chapter map (page → "ROZDZIAŁ N TYTUŁ")
  - Sourcing rough text for downstream card generation
  - Citation: "Murray, rozdz. N, s. P" — chapter + page from the index

Output: _work/textbook_parsed.json with shape:
  {
    "source": "2018_murray_mikrobiologia.pdf",
    "page_count": int,
    "chapters": [
      {"num": int, "title": str, "page_start": int, "page_end": int}, ...
    ],
    "pages": [
      {"page": int (1-indexed), "chapter_num": int|null, "text": str}, ...
    ]
  }

NO LLM. Pure pypdf.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pypdf import PdfReader

TEXTBOOK_PDF = Path(__file__).resolve().parent.parent / "_work" / "2018_murray_mikrobiologia.pdf"
OUT_JSON = Path(__file__).resolve().parent.parent / "_work" / "textbook_parsed.json"


# Chapter heading pattern. Examples seen in recon:
#   "ROZDZIAŁ 7 ELEMENTY REAKCJI OBRONNYCH GOSPODARZA"
#   "ROZDZIAŁ 17 ANTYBIOTYKI"
#   "ROZDZIAŁ 43 LUDZKIE HERPESWIRUSY"
CHAPTER_RE = re.compile(
    r"\bROZDZIAŁ\s+(\d+)\s+([A-ZŻŚĆŁÓŹŃĄĘ][A-ZŻŚĆŁÓŹŃĄĘ\s\-,()]{4,80})",
    re.UNICODE,
)


def detect_chapter_on_page(text: str) -> tuple[int, str] | None:
    """Return (chapter_num, title) if a chapter heading appears on this page,
    else None. Picks the first match; same heading often appears as running
    title across many pages, but we use it just to mark chapter boundaries."""
    m = CHAPTER_RE.search(text)
    if not m:
        return None
    num = int(m.group(1))
    title = m.group(2).strip()
    # Strip very short fragments and obvious page-number trailers
    title = re.sub(r"\s+\d+\s*$", "", title).strip()
    return (num, title)


def main():
    print(f"opening {TEXTBOOK_PDF.name} ({TEXTBOOK_PDF.stat().st_size / 1e6:.0f} MB)...")
    reader = PdfReader(str(TEXTBOOK_PDF))
    n = len(reader.pages)
    print(f"  {n} pages, extracting...")

    pages_data = []
    # Track chapters by first occurrence — running titles will repeat the same
    # header many times; we keep the FIRST page where each chapter number
    # appears as its page_start.
    chapter_first_page: dict[int, dict] = {}
    current_chapter_num: int | None = None

    for i in range(n):
        if i % 50 == 0:
            print(f"  page {i + 1}/{n}")
        try:
            text = reader.pages[i].extract_text() or ""
        except Exception as e:
            text = ""
        page_num = i + 1
        detect = detect_chapter_on_page(text)
        chapter_num_for_page = current_chapter_num
        if detect:
            num, title = detect
            if num not in chapter_first_page:
                chapter_first_page[num] = {"num": num, "title": title, "page_start": page_num}
            # The "current chapter" only changes when we encounter a higher-number
            # chapter than the running one (forward progression) — guards against
            # running-title repetitions that go backwards on some pages.
            if current_chapter_num is None or num >= current_chapter_num:
                current_chapter_num = num
                chapter_num_for_page = num
        pages_data.append({"page": page_num, "chapter_num": chapter_num_for_page, "text": text})

    # Compute page_end per chapter: page_end = next chapter's page_start - 1,
    # or total pages for the last chapter.
    chapters_sorted = sorted(chapter_first_page.values(), key=lambda c: c["num"])
    for idx, ch in enumerate(chapters_sorted):
        if idx + 1 < len(chapters_sorted):
            ch["page_end"] = chapters_sorted[idx + 1]["page_start"] - 1
        else:
            ch["page_end"] = n

    output = {
        "source": TEXTBOOK_PDF.name,
        "page_count": n,
        "chapters": chapters_sorted,
        "pages": pages_data,
        "stats": {
            "total_pages": n,
            "pages_with_text": sum(1 for p in pages_data if p["text"].strip()),
            "total_chapters_detected": len(chapters_sorted),
            "total_chars": sum(len(p["text"]) for p in pages_data),
        },
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"wrote {OUT_JSON}  ({OUT_JSON.stat().st_size / 1e6:.1f} MB)")
    print(f"  pages with text:       {output['stats']['pages_with_text']}")
    print(f"  chapters detected:     {output['stats']['total_chapters_detected']}")
    print(f"  total chars:           {output['stats']['total_chars']:,}")
    print(f"  chapters:")
    for c in chapters_sorted[:20]:
        print(f"    rozdz. {c['num']:>2}  s.{c['page_start']:>3}-{c['page_end']:<3}  {c['title']}")
    if len(chapters_sorted) > 20:
        print(f"    ... and {len(chapters_sorted) - 20} more")


if __name__ == "__main__":
    main()

"""Extract structured Q&A from baza_mikrobiologia.pdf (Google Docs MCQ export).

Rules discovered by recon:
- Question text + correct answer share Spectral-Bold font.
- Wrong answers use Spectral-Regular.
- Student annotations use red text (any color with R>0.5, G<0.3, B<0.3) and
  often appear inline next to the relevant option.
- Each question header ends with ":" or "?" (or sometimes neither — fallback
  is "first bold line not preceded by an option letter").
- Options are prefixed `a.`, `b.`, ... `f.` (sometimes the letter+dot is
  Regular font even when the option body is Bold — see Metachromazja example
  on page 11). We classify "bold-ness" by sampling the body, skipping the
  letter prefix.

Topics come from the TOC on pages 2-3 (e.g. "DEZYNFEKCJA I STERYLIZACJA 31").

Output: _work/bank_parsed.json with shape:
  {
    "source": "baza_mikrobiologia.pdf",
    "topics": [{"name": str, "page_start": int, "page_end": int}, ...],
    "questions": [
      {
        "page": int,
        "topic": str,
        "stem": str,                    # the question text
        "options": [
          {"letter": "a", "text": str, "correct": bool, "annotation": str},
          ...
        ],
        "raw_red_around": str           # red text in the question block, not bound to an option
      },
      ...
    ]
  }

NO LLM calls. Pure pdfplumber + regex.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import pdfplumber

BANK_PDF = Path(__file__).resolve().parent.parent / "_work" / "baza_mikrobiologia.pdf"
OUT_JSON = Path(__file__).resolve().parent.parent / "_work" / "bank_parsed.json"


# ---------- color / font helpers ----------


def is_bold(fontname: str | None) -> bool:
    return "Bold" in (fontname or "")


def is_red_color(c) -> bool:
    """Color tuple → True if it's a red shade (student annotations)."""
    if isinstance(c, list):
        c = tuple(c)
    if not isinstance(c, tuple) or len(c) != 3:
        return False
    r, g, b = c
    return r > 0.5 and g < 0.3 and b < 0.3


# ---------- TOC parsing ----------


TOC_TOPICS = [
    # Hardcoded from recon of pages 2-3. The TOC in the bank has these entries
    # in this exact order with these page starts; the topic ends where the
    # next one begins. Maintaining this as a constant avoids fuzzy TOC parsing.
    ("BAKTERIE — PODSTAWY, PODŁOŻA, BARWIENIA, DIAGNOSTYKA", 3),
    ("DEZYNFEKCJA I STERYLIZACJA", 31),
    ("PRĄTKI, LASECZKI, CORYNEBACTERIACEAE", 55),
    ("ZIARENKOWCE GRAM-DODATNIE I GRAM-UJEMNE", 94),
    ("ANTYBIOTYKI", 117),
    ("KRĘTKI I CHOROBY WENERYCZNE", 125),
    ("PAŁECZKI, KRĘTKI GRAM-UJEMNE", 143),
    ("WIRUSY — PODSTAWY, HODOWLE, DIAGNOSTYKA", 189),
    ("WIRUSOLOGIA SZCZEGÓŁOWA", 207),
    ("GRZYBY", 224),
    ("INNE/MIX", 229),
    ("ZAKAŻENIA UKŁADU MOCZOWEGO", 230),
    ("PARAZYTOLOGIA", 233),
    # 248 = KONIEC, no content after
]


def topic_for_page(page_num: int) -> str:
    """Map 1-indexed page number → topic name. Pages 1-2 = TOC/preamble."""
    if page_num < 3:
        return "TOC"
    last_topic = TOC_TOPICS[0][0]
    for name, start in TOC_TOPICS:
        if page_num >= start:
            last_topic = name
        else:
            break
    return last_topic


# ---------- per-page line grouping ----------


def group_lines(page) -> list[list[dict]]:
    """Group page.chars into lines by y-coordinate. Returns lines sorted top→bottom,
    each line a list of chars sorted left→right."""
    bins: dict[int, list[dict]] = defaultdict(list)
    for ch in page.chars:
        # Round y to nearest 2pt to merge slightly-offset chars onto the same line.
        y_bin = round(ch["top"] / 2) * 2
        bins[y_bin].append(ch)
    lines = []
    for y in sorted(bins.keys()):
        chs = sorted(bins[y], key=lambda c: c["x0"])
        # Skip page-number line at very top (single digit/number + spaces, y<50)
        if y < 50:
            text = "".join(c["text"] for c in chs).strip()
            if text.isdigit():
                continue
        # Skip bottom watermark / footer (often y>720)
        if y > 720:
            text = "".join(c["text"] for c in chs).strip()
            if text in ("DP", "") or len(text) < 4:
                continue
        lines.append(chs)
    return lines


def line_to_text(chs: list[dict]) -> str:
    return "".join(c["text"] for c in chs)


def line_runs(chs: list[dict]) -> list[tuple[str, tuple, str]]:
    """Within a line, group consecutive chars sharing (fontname, color)."""
    runs = []
    if not chs:
        return runs
    cur_font = None
    cur_color = None
    cur_text = ""
    for ch in chs:
        fn = ch.get("fontname", "")
        co = ch.get("non_stroking_color", None)
        co = tuple(co) if isinstance(co, list) else co
        if fn == cur_font and co == cur_color:
            cur_text += ch["text"]
        else:
            if cur_text:
                runs.append((cur_font, cur_color, cur_text))
            cur_font = fn
            cur_color = co
            cur_text = ch["text"]
    if cur_text:
        runs.append((cur_font, cur_color, cur_text))
    return runs


# ---------- question classification ----------


OPTION_PREFIX_RE = re.compile(r"^([a-fA-F])[.\s]\s*(.*)$")


def line_starts_option(text: str) -> tuple[str, str] | None:
    """Returns (letter, remaining_text) if line starts with `a.`/`b.`/... else None."""
    m = OPTION_PREFIX_RE.match(text.strip())
    if m:
        return m.group(1).lower(), m.group(2)
    return None


def chars_predominantly_bold(chs: list[dict], skip_prefix_chars: int = 0) -> bool:
    """Excluding the first N chars (option letter prefix), is the bulk of the
    remaining text in a Bold font?"""
    relevant = chs[skip_prefix_chars:] if skip_prefix_chars else chs
    if not relevant:
        return False
    bold_count = sum(1 for c in relevant if is_bold(c.get("fontname")) and c["text"].strip())
    nonspace = sum(1 for c in relevant if c["text"].strip())
    if nonspace == 0:
        return False
    return bold_count / nonspace >= 0.6


def line_red_text(chs: list[dict]) -> str:
    """Return only the red-colored characters in a line, concatenated."""
    parts = []
    for c in chs:
        co = c.get("non_stroking_color", None)
        co = tuple(co) if isinstance(co, list) else co
        if is_red_color(co):
            parts.append(c["text"])
    return "".join(parts).strip()


# ---------- main extractor ----------


def extract_stream(pages) -> list[dict]:
    """Walk lines across ALL pages as a flat stream. Questions span page breaks
    naturally — no per-page artifacts.

    State machine: NEUTRAL → ACCUMULATING_STEM → COLLECTING_OPTIONS → NEUTRAL
    """
    # Build the master line list with (page_num, chars) tuples
    master_lines: list[tuple[int, list[dict]]] = []
    for i, page in enumerate(pages):
        page_num = i + 1
        if page_num < 3 or page_num >= 248:
            continue
        for chs in group_lines(page):
            master_lines.append((page_num, chs))

    questions = []
    state = "NEUTRAL"
    cur_stem = ""
    cur_options: list[dict] = []
    cur_raw_red = []
    cur_option_chs: list[dict] | None = None  # accumulating chs for multi-line current option
    cur_option_letter: str | None = None
    cur_option_pre_len = 0  # how many chars of the option line are the prefix
    cur_stem_page: int | None = None  # page where stem started

    def flush_question():
        nonlocal cur_stem, cur_options, cur_raw_red, cur_stem_page
        stem = cur_stem.strip()
        if stem and cur_options:
            p = cur_stem_page if cur_stem_page else 0
            questions.append({
                "page": p,
                "topic": topic_for_page(p),
                "stem": stem,
                "options": cur_options,
                "raw_red_around": " | ".join(s for s in cur_raw_red if s),
            })
        cur_stem = ""
        cur_options = []
        cur_raw_red = []
        cur_stem_page = None

    def flush_option():
        nonlocal cur_option_chs, cur_option_letter, cur_option_pre_len
        if cur_option_chs is None or cur_option_letter is None:
            return
        # Body text without the prefix
        body_chs = cur_option_chs[cur_option_pre_len:]
        # Strip leading/trailing whitespace chars on the body for the bold check
        meaningful = [c for c in body_chs if c["text"].strip()]
        is_correct = False
        if meaningful:
            bold_count = sum(1 for c in meaningful if is_bold(c.get("fontname")))
            is_correct = bold_count / len(meaningful) >= 0.6
        text_full = "".join(c["text"] for c in body_chs).strip()
        annotation = "".join(
            c["text"] for c in body_chs
            if is_red_color(c.get("non_stroking_color"))
        ).strip()
        # Strip annotation out of body text if it's contiguous at the end (heuristic)
        text_clean = text_full
        if annotation and annotation in text_full:
            text_clean = text_full.replace(annotation, "").strip()
        cur_options.append({
            "letter": cur_option_letter,
            "text": text_clean,
            "correct": is_correct,
            "annotation": annotation,
        })
        cur_option_chs = None
        cur_option_letter = None
        cur_option_pre_len = 0

    for page_num, chs in master_lines:
        text = line_to_text(chs).strip()
        if not text:
            continue
        opt = line_starts_option(text)
        if opt is not None:
            letter, remaining = opt
            # In NEUTRAL state, an option with no preceding stem is orphan
            # debris (e.g. continuation of an already-flushed question). Skip.
            if state == "NEUTRAL":
                continue
            # If we were accumulating stem, finalize it; transition to options mode
            if state == "ACCUMULATING_STEM":
                state = "COLLECTING_OPTIONS"
            # Heuristic: if we see option "a." while already collecting options
            # for a prior letter, it's likely a NEW question with no explicit
            # stem on its own line (rare in this bank, but happens). Without a
            # new stem the second question can't be parsed; just flush previous.
            elif state == "COLLECTING_OPTIONS" and letter == "a" and cur_options:
                # Looks like a fresh question started without an explicit bold
                # stem line. Flush what we have and reset to NEUTRAL — the
                # orphan option will be skipped on next iteration since state
                # is NEUTRAL.
                flush_option()
                flush_question()
                continue
            # Flush previous option if any
            flush_option()
            # Begin new option. Calculate prefix length (chars consumed by "a." + space).
            cur_option_letter = letter
            full_line_text = line_to_text(chs)
            # Match the prefix in the actual line text to count prefix chars
            m = OPTION_PREFIX_RE.match(full_line_text.strip())
            if m:
                prefix_str = full_line_text.strip()[:m.start(2)] if m.group(2) else full_line_text.strip()
                # Find how many original chars correspond to that prefix
                # — easier: count chars until we've passed the dot + first space.
                count = 0
                seen_letter = False
                seen_dot = False
                for i, c in enumerate(chs):
                    if c["text"].strip() == "":
                        if seen_dot:
                            count = i + 1
                            break
                        count = i + 1
                        continue
                    if not seen_letter and c["text"].strip().lower() == letter:
                        seen_letter = True
                        count = i + 1
                        continue
                    if seen_letter and c["text"] == "." and not seen_dot:
                        seen_dot = True
                        count = i + 1
                        continue
                    if seen_dot:
                        count = i
                        break
                cur_option_pre_len = count
            else:
                cur_option_pre_len = 0
            cur_option_chs = list(chs)
            continue

        # Non-option line: either part of stem, part of previous option (wrap),
        # or a stand-alone annotation.
        if state == "NEUTRAL":
            # Look for a question start: predominantly bold line, not an option.
            if chars_predominantly_bold(chs):
                cur_stem = text
                cur_stem_page = page_num
                state = "ACCUMULATING_STEM"
                cur_raw_red = [s for s in [line_red_text(chs)] if s]
            # else: stray text between questions — drop
        elif state == "ACCUMULATING_STEM":
            # Continue accumulating bold lines until we see an option
            if chars_predominantly_bold(chs):
                cur_stem += " " + text
                r = line_red_text(chs)
                if r:
                    cur_raw_red.append(r)
            else:
                # Mixed/regular line in stem area — likely short stem continuation
                if len(text) < 80:
                    cur_stem += " " + text
                # else: drop (probably noise / orphan option line)
        elif state == "COLLECTING_OPTIONS":
            # Wrap of current option OR end-of-question + new question's bold line
            if chars_predominantly_bold(chs) and len(text) > 25:
                # New question starting (bold, substantive — likely a stem)
                flush_option()
                flush_question()
                cur_stem = text
                cur_stem_page = page_num
                state = "ACCUMULATING_STEM"
                cur_raw_red = [s for s in [line_red_text(chs)] if s]
            elif cur_option_chs is not None:
                # Wrap of current option (multi-line)
                cur_option_chs.extend(chs)
            else:
                # Stray annotation; capture if red
                r = line_red_text(chs)
                if r:
                    cur_raw_red.append(r)

    # End-of-stream: flush any in-progress question
    flush_option()
    flush_question()
    return questions


def main():
    with pdfplumber.open(str(BANK_PDF)) as pdf:
        print(f"  parsing {len(pdf.pages)} pages as flat stream...")
        all_questions = extract_stream(pdf.pages)

    output = {
        "source": BANK_PDF.name,
        "topics": [{"name": n, "page_start": p} for n, p in TOC_TOPICS],
        "questions": all_questions,
        "stats": {
            "total_questions": len(all_questions),
            "by_topic": _by_topic_count(all_questions),
            "questions_with_correct_answer": sum(
                1 for q in all_questions if any(o["correct"] for o in q["options"])
            ),
            "questions_with_red_annotation": sum(
                1 for q in all_questions
                if q["raw_red_around"] or any(o["annotation"] for o in q["options"])
            ),
        },
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"wrote {OUT_JSON}")
    print(f"  total questions:                  {output['stats']['total_questions']}")
    print(f"  with a marked correct answer:     {output['stats']['questions_with_correct_answer']}")
    print(f"  with red annotation(s):           {output['stats']['questions_with_red_annotation']}")
    print(f"  by topic:")
    for topic, count in output["stats"]["by_topic"].items():
        print(f"    {count:>5}  {topic}")


def _by_topic_count(questions):
    out = {}
    for q in questions:
        out[q["topic"]] = out.get(q["topic"], 0) + 1
    return out


if __name__ == "__main__":
    main()

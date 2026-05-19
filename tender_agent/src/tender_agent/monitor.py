"""Daily-monitor + fit-score ranking for BZP announcements.

Phase 0.7 Phase 1-step-2 deliverable: given a CPV-prefix watchlist and
a firm profile, fetch the last N days of BZP announcements, score each
against firm fit, and emit a ranked Markdown digest (plus one JSONL
line per scored item for future Discord/email-digest wiring).

The score is a transparent heuristic (not ML — Phase 0.7 is plumbing):

  cpv_score        (0-100)  main CPV prefix match → 100; additional → 60
  deadline_score   (-100..100)  >=14d=100, 7-14d=60, 3-7d=30, <3d=0, past=-100
  criterion_bonus  (0-50)   non-price weight: 100% cena=0, <60% cena=50

  total = cpv + deadline + criterion_bonus   (max 250)

Heuristic justification:

- CPV match is the binary filter the operator's already doing manually;
  prefix-matching reproduces it programmatically with score, not 0/1.
- Deadline runway prevents the digest from highlighting tenders the
  firm couldn't realistically respond to. < 3 days = effectively dead.
- Criterion-mix bonus surfaces tenders where non-price differentiation
  matters (gwarancja, doświadczenie, termin). A pure 100%-cena tender
  is a race to the bottom; small firms rarely win those without scale.

The score is **per-firm-fit**, not "tender quality". Two firms with
different profiles will rank the same fetched set differently. The
firm profile carries no CPV preferences yet (FirmProfile model is
Phase 0 minimal); CPV prefixes come from the CLI, not the profile.
Phase 1 expands FirmProfile with `preferred_cpv_prefixes` so the call
becomes single-source.

Usage:

  python -m tender_agent.monitor \\
      --days 7 \\
      --cpv 72,48,30,71 \\
      --firm _samples/curated/firm_demo.json \\
      --out _samples/monitor/2026-05-19-it.md \\
      --limit 20

Outputs (always two files alongside each other):

  <out>.md     — Markdown digest, ranked table + top-N detail blocks
  <out>.jsonl  — one JSON line per scored announcement (machine-feed)

The module exports `score_announcement`, `collect`, `run_monitor` for
programmatic use (Discord digest cron, etc.).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .fetch import search
from .models import FirmProfile


# ---------------------------------------------------------------------------
# Scoring


@dataclass(frozen=True)
class ScoreBreakdown:
    """Transparent score components. Saved verbatim into the JSONL so
    a future tuning pass (or the operator) can see WHY a given tender
    ranked high or low, not just the total."""
    cpv: int
    deadline: int
    criterion_bonus: int
    total: int


@dataclass(frozen=True)
class ScoredItem:
    """One ranked announcement. Holds the raw BZP dict so the digest
    renderer can fish out any other field without re-fetching."""
    bzp_number: str
    score: ScoreBreakdown
    raw: dict[str, Any]


def parse_cpv_codes(cpv_code_field: str) -> list[str]:
    """`"72263000-6 (Usługi wdrażania oprogramowania),72250000-2 (..."`
    → `["72263000-6", "72250000-2", ...]`.

    The BZP `cpvCode` field is a comma-separated list with each code
    followed by a space-prefixed parenthesised label. We split on commas,
    then take everything before the first space — the bare CPV code
    (8 digits + check digit suffix).
    """
    if not cpv_code_field:
        return []
    codes: list[str] = []
    for part in cpv_code_field.split(","):
        part = part.strip()
        if not part:
            continue
        code = part.split(" ", 1)[0]
        codes.append(code)
    return codes


def score_cpv(cpv_codes: list[str], prefixes: list[str]) -> int:
    """100 if main code (first in list) matches a prefix; 60 if only
    a non-main code matches; 0 otherwise."""
    if not cpv_codes:
        return 0
    main = cpv_codes[0]
    if any(main.startswith(p) for p in prefixes):
        return 100
    if any(c.startswith(p) for c in cpv_codes[1:] for p in prefixes):
        return 60
    return 0


def _parse_bzp_datetime(s: Optional[str]) -> Optional[datetime]:
    """BZP returns ISO with trailing `Z` (UTC). Parse + make tz-aware."""
    if not s:
        return None
    # Some entries use `Z`, some use `+00:00`. fromisoformat handles +00:00
    # since 3.11; replace Z for older safety.
    cleaned = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def score_deadline(
    submitting_offers_iso: Optional[str],
    now: Optional[datetime] = None,
) -> int:
    """Days of runway between now and the bid deadline → bucketed score."""
    deadline = _parse_bzp_datetime(submitting_offers_iso)
    if deadline is None:
        return 50  # unknown deadline = neutral; don't penalise good CPV match
    now_dt = now or datetime.now(timezone.utc)
    days = (deadline - now_dt).total_seconds() / 86400.0
    if days < 0:
        return -100
    if days >= 14:
        return 100
    if days >= 7:
        return 60
    if days >= 3:
        return 30
    return 0


def score_criteria(raw_item: dict[str, Any]) -> int:
    """The BZP search response doesn't include the criteria breakdown —
    that lives in the per-announcement HTML body, which we don't fetch
    in monitor mode (would explode the cost). Return neutral 25 when
    criteria are absent from the dict. Phase 1 enhancement: a single
    head request per top-K item to fetch the body and re-score with
    actual criteria. For now this is "future signal hook"."""
    criteria = raw_item.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        return 25
    # Walk the list looking for a "cena" entry with a numeric weight.
    cena_weight: Optional[float] = None
    for c in criteria:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").lower()
        if "cena" in name:
            try:
                cena_weight = float(c.get("weight") or 0)
            except (TypeError, ValueError):
                continue
            break
    if cena_weight is None:
        return 50  # no cena criterion = strong non-price differentiation
    if cena_weight >= 100:
        return 0
    if cena_weight >= 80:
        return 10
    if cena_weight >= 60:
        return 30
    return 50


def score_announcement(
    item: dict[str, Any],
    cpv_prefixes: list[str],
    now: Optional[datetime] = None,
) -> ScoreBreakdown:
    """Score one raw BZP item against the firm's CPV watchlist."""
    cpv_codes = parse_cpv_codes(item.get("cpvCode") or "")
    cpv = score_cpv(cpv_codes, cpv_prefixes)
    deadline = score_deadline(item.get("submittingOffersDate"), now=now)
    crit = score_criteria(item)
    return ScoreBreakdown(
        cpv=cpv, deadline=deadline, criterion_bonus=crit,
        total=cpv + deadline + crit,
    )


# ---------------------------------------------------------------------------
# Collection + ranking


def collect(
    cpv_prefixes: list[str],
    days: int,
    *,
    page_size: int = 200,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One BZP API call, client-side multi-prefix filter.

    Returns `(all_items, filtered_items)` so the digest can report both
    "we scanned N tenders" and "K matched the CPV watchlist".

    Caveats:
    - BZP search caps at page_size=200 (silently — per Phase 0 HANDOFF).
      If a wide window returns 200, we may be missing older items;
      digest header warns when this happens. Phase 1 fix: loop date
      windows.
    - The cpvCode field on items returned by `/notice` search is the
      same comma-list with labels as the per-record dict, so the same
      `parse_cpv_codes` handles both.
    """
    items = search(
        page_size=page_size,
        date_from=date.today() - timedelta(days=days),
        date_to=date.today(),
    )
    filtered: list[dict[str, Any]] = []
    for it in items:
        cpv_codes = parse_cpv_codes(it.get("cpvCode") or "")
        if not cpv_codes:
            continue
        if any(c.startswith(p) for c in cpv_codes for p in cpv_prefixes):
            filtered.append(it)
    return items, filtered


def rank(
    items: list[dict[str, Any]],
    cpv_prefixes: list[str],
    *,
    now: Optional[datetime] = None,
) -> list[ScoredItem]:
    """Score every item and return descending by `score.total`."""
    scored: list[ScoredItem] = []
    for it in items:
        breakdown = score_announcement(it, cpv_prefixes, now=now)
        scored.append(ScoredItem(
            bzp_number=it.get("bzpNumber", ""),
            score=breakdown,
            raw=it,
        ))
    scored.sort(key=lambda s: s.score.total, reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Digest rendering


def _fmt_deadline_pl(iso: Optional[str], now: Optional[datetime] = None) -> str:
    """Polish-friendly deadline rendering with days remaining."""
    if not iso:
        return "nie podano"
    dt = _parse_bzp_datetime(iso)
    if dt is None:
        return iso
    now_dt = now or datetime.now(timezone.utc)
    days = (dt - now_dt).total_seconds() / 86400.0
    days_label = f"({days:+.1f}d)"
    # Show in UTC for now — drafter renders Europe/Warsaw via fmt_pl_datetime,
    # but the monitor digest is for skim-not-quote so UTC is fine and avoids
    # importing the drafter's zoneinfo helper here.
    return f"{dt.strftime('%Y-%m-%d %H:%M')}Z {days_label}"


def render_markdown(
    scored: list[ScoredItem],
    *,
    cpv_prefixes: list[str],
    days: int,
    firm: FirmProfile,
    total_fetched: int,
    capped_at_page_size: bool,
    limit: int,
    now: Optional[datetime] = None,
) -> str:
    """Render the digest as Markdown."""
    now_dt = now or datetime.now(timezone.utc)
    head = [
        f"# BZP monitor — CPV {','.join(cpv_prefixes)}",
        f"",
        f"- **Generated:** {now_dt.strftime('%Y-%m-%d %H:%M')}Z",
        f"- **Window:** {(now_dt.date() - timedelta(days=days)).isoformat()} .. {now_dt.date().isoformat()} ({days} days)",
        f"- **Firm:** {firm.short_name} (NIP {firm.nip})",
        f"- **Fetched:** {total_fetched} tenders",
        f"- **Matched CPV watchlist:** {len(scored)}",
        f"- **Showing top:** {min(limit, len(scored))}",
    ]
    if capped_at_page_size:
        head.append(
            "- ⚠️ **API page-size cap hit at 200** — older window items "
            "may be missing. Loop date windows in Phase 1 (HANDOFF.md gap)."
        )

    head.extend([
        "",
        "## Ranking",
        "",
        "| # | BZP | CPV main | Object | Org | Deadline | Score |",
        "|---|---|---|---|---|---|---|",
    ])

    top = scored[:limit]
    for i, item in enumerate(top, start=1):
        cpv_codes = parse_cpv_codes(item.raw.get("cpvCode") or "")
        main_cpv = cpv_codes[0] if cpv_codes else "?"
        obj = (item.raw.get("orderObject") or "")[:60].replace("|", "/")
        org = (item.raw.get("organizationName") or "")[:35].replace("|", "/")
        deadline_str = _fmt_deadline_pl(
            item.raw.get("submittingOffersDate"), now=now_dt
        ).replace("|", "/")
        head.append(
            f"| {i} | {item.bzp_number} | {main_cpv} | {obj} | {org} | "
            f"{deadline_str} | **{item.score.total}** "
            f"({item.score.cpv}+{item.score.deadline}+{item.score.criterion_bonus}) |"
        )

    head.extend(["", "## Top-N detail", ""])
    for i, item in enumerate(top, start=1):
        cpv_codes = parse_cpv_codes(item.raw.get("cpvCode") or "")
        head.extend([
            f"### #{i} — {item.bzp_number} (score {item.score.total})",
            "",
            f"- **CPV codes:** {', '.join(cpv_codes) or '(none)'}",
            f"- **Score breakdown:** "
            f"CPV={item.score.cpv} + deadline={item.score.deadline} + "
            f"criteria={item.score.criterion_bonus}",
            f"- **Object:** {item.raw.get('orderObject') or '(brak)'}",
            f"- **Organization:** {item.raw.get('organizationName') or '(brak)'} "
            f"(NIP {item.raw.get('organizationNationalId') or '?'})",
            f"- **Order type:** {item.raw.get('orderType') or '?'}",
            f"- **Deadline:** {_fmt_deadline_pl(item.raw.get('submittingOffersDate'), now=now_dt)}",
            f"- **Published:** {(item.raw.get('publicationDate') or '')[:10] or '?'}",
            f"- **Below EU threshold:** {item.raw.get('isTenderAmountBelowEU')!r}",
            "",
            f"  Pull full record + draft: `python -m tender_agent.cli draft '{item.bzp_number}' --siwz auto`",
            "",
        ])

    return "\n".join(head) + "\n"


def render_jsonl(scored: list[ScoredItem]) -> str:
    """One line per scored item, ordered by rank. Trimmed to the fields
    a Discord/email digest hook is likely to need; raw cpvCode kept so
    consumers can re-parse if they want different prefix logic."""
    lines: list[str] = []
    for item in scored:
        entry = {
            "bzp_number": item.bzp_number,
            "score": asdict(item.score),
            "cpv_code": item.raw.get("cpvCode"),
            "object": item.raw.get("orderObject"),
            "organization": item.raw.get("organizationName"),
            "organization_nip": item.raw.get("organizationNationalId"),
            "order_type": item.raw.get("orderType"),
            "submitting_offers_date": item.raw.get("submittingOffersDate"),
            "publication_date": item.raw.get("publicationDate"),
            "is_below_eu": item.raw.get("isTenderAmountBelowEU"),
        }
        lines.append(json.dumps(entry, ensure_ascii=False))
    return "\n".join(lines) + ("\n" if lines else "")


# ---------------------------------------------------------------------------
# Top-level orchestrator


@dataclass(frozen=True)
class MonitorResult:
    scored: list[ScoredItem]
    total_fetched: int
    capped: bool


def run_monitor(
    cpv_prefixes: list[str],
    days: int,
    *,
    page_size: int = 200,
    now: Optional[datetime] = None,
) -> MonitorResult:
    """Fetch → filter → rank. Reusable by a future Discord/cron caller."""
    all_items, filtered = collect(cpv_prefixes, days, page_size=page_size)
    scored = rank(filtered, cpv_prefixes, now=now)
    return MonitorResult(
        scored=scored,
        total_fetched=len(all_items),
        capped=len(all_items) >= page_size,
    )


# ---------------------------------------------------------------------------
# CLI


def _load_firm(path: Path) -> FirmProfile:
    return FirmProfile.model_validate_json(path.read_text(encoding="utf-8"))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tender-agent-monitor",
        description="Daily BZP digest with CPV-watchlist fit-score ranking.",
    )
    p.add_argument(
        "--days", type=int, default=1,
        help="Days back from today to fetch (default 1 — typical cron cadence).",
    )
    p.add_argument(
        "--cpv", default="72,48,30",
        help=(
            "Comma-separated CPV prefixes. Default '72,48,30' = IT services "
            "+ software packages + hardware (the IT-consultancy watchlist). "
            "CPV 71 was tested 2026-05-19 and dropped — most 71* hits are "
            "civil engineering / road / bridge work, not IT-adjacent."
        ),
    )
    p.add_argument(
        "--firm", required=True,
        help="Path to FirmProfile JSON (e.g. _samples/curated/firm_demo.json).",
    )
    p.add_argument(
        "--out", required=True,
        help="Output path; the .md digest is written there, the .jsonl is "
             "written alongside with '.jsonl' suffix.",
    )
    p.add_argument(
        "--limit", type=int, default=20,
        help="Max top-N items to include in the digest detail (default 20).",
    )
    p.add_argument(
        "--page-size", type=int, default=200,
        help="BZP page size cap (silent server-side max is 200).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cpv_prefixes = [p.strip() for p in args.cpv.split(",") if p.strip()]
    if not cpv_prefixes:
        print("--cpv produced an empty prefix list", file=sys.stderr)
        return 2

    firm_path = Path(args.firm)
    if not firm_path.exists():
        print(f"firm profile not found: {firm_path}", file=sys.stderr)
        return 2
    firm = _load_firm(firm_path)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_path.with_suffix(out_path.suffix + ".jsonl")

    result = run_monitor(cpv_prefixes, args.days, page_size=args.page_size)

    md = render_markdown(
        result.scored,
        cpv_prefixes=cpv_prefixes,
        days=args.days,
        firm=firm,
        total_fetched=result.total_fetched,
        capped_at_page_size=result.capped,
        limit=args.limit,
    )
    out_path.write_text(md, encoding="utf-8")

    jsonl = render_jsonl(result.scored)
    jsonl_path.write_text(jsonl, encoding="utf-8")

    print(
        f"fetched={result.total_fetched} "
        f"matched={len(result.scored)} "
        f"top1_score={result.scored[0].score.total if result.scored else 'n/a'} "
        f"out={out_path} "
        f"jsonl={jsonl_path}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

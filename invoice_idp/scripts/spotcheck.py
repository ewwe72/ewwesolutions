"""Spotcheck eval results — operator manually verifies extraction accuracy.

For each JSON in eval_set/_results/:
  1. Open the source PDF in the default viewer
  2. Print a compact summary of the extracted header + totals + warnings
  3. Ask for two verdicts (header / totals) and an optional note
  4. Save to eval_set/_results/_verdicts.json after each entry (resume-safe)

At the end, aggregate against the Phase 1 §15 gate:
  - ≥85% "good" on header fields
  - ≥90% "good" on totals

Usage:
    python scripts/spotcheck.py
    python scripts/spotcheck.py --resume    # skip already-reviewed
    python scripts/spotcheck.py --no-open   # don't auto-open PDFs
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "eval_set" / "_results"
VERDICTS_PATH = RESULTS_DIR / "_verdicts.json"
PDF_SEARCH_DIRS = [
    ROOT / "eval_set",
    ROOT / "eval_set" / "_noise",
    ROOT / "eval_set" / "_dupes",
]


@dataclass
class Verdict:
    pdf: str
    header: str       # 'g' / 'm' / 'b'
    totals: str
    notes: str
    timestamp: str


def find_pdf(filename: str) -> Path | None:
    for d in PDF_SEARCH_DIRS:
        candidate = d / filename
        if candidate.exists():
            return candidate
    return None


def open_pdf(path: Path) -> None:
    try:
        os.startfile(str(path))  # Windows
    except AttributeError:
        import subprocess
        subprocess.Popen(["xdg-open", str(path)])


def fmt_money(m: dict[str, Any] | None) -> str:
    if not m:
        return "(null)"
    return f"{m.get('amount')} {m.get('currency', '')}"


def display(result_path: Path, payload: dict[str, Any]) -> None:
    inv = payload
    e = payload.get("__eval__", {})
    line = "─" * 64
    print(f"\n{line}")
    print(f"{result_path.stem}.pdf")
    print(line)
    if e:
        sonnet = "-" if e.get("sonnet_conf") is None else f"{e['sonnet_conf']:.2f}"
        haiku = e.get("haiku_conf")
        haiku_s = f"{haiku:.2f}" if haiku is not None else "-"
        print(
            f"Routing: {e.get('path_taken', '?'):<18}  "
            f"haiku={haiku_s}  sonnet={sonnet}  cost=${e.get('cost_usd', 0):.4f}"
        )
    print()
    print("HEADER")
    print(f"  invoice_number   {inv.get('invoice_number')}")
    print(f"  invoice_type     {inv.get('invoice_type')}")
    print(f"  issue_date       {inv.get('issue_date')}")
    print(f"  sale_date        {inv.get('sale_date')}")
    seller = inv.get("seller") or {}
    buyer = inv.get("buyer") or {}
    print(f"  seller.name      {seller.get('name')}")
    print(f"  seller.nip       {seller.get('nip')}")
    print(f"  buyer.name       {buyer.get('name')}")
    print(f"  buyer.nip        {buyer.get('nip')}")
    print()
    print("TOTALS")
    print(f"  total_net        {fmt_money(inv.get('total_net'))}")
    print(f"  total_vat        {fmt_money(inv.get('total_vat'))}")
    print(f"  total_gross      {fmt_money(inv.get('total_gross'))}")
    print(f"  lines            {len(inv.get('lines') or [])}")
    warnings = inv.get("extraction_warnings") or []
    if warnings:
        print()
        print(f"WARNINGS ({len(warnings)})")
        for w in warnings:
            print(f"  {w}")


def ask_choice(label: str, options: dict[str, str]) -> str:
    legend = "/".join(f"{k}={v}" for k, v in options.items())
    while True:
        ans = input(f"  {label} [{legend}]: ").strip().lower()
        if ans in options:
            return ans


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Spotcheck Phase 1 eval results.")
    parser.add_argument("--resume", action="store_true",
                        help="Skip files already in _verdicts.json")
    parser.add_argument("--no-open", action="store_true",
                        help="Don't auto-open PDFs in the default viewer")
    args = parser.parse_args(argv)

    verdicts: dict[str, dict[str, Any]] = {}
    if VERDICTS_PATH.exists():
        existing = json.loads(VERDICTS_PATH.read_text(encoding="utf-8"))
        verdicts = {v["pdf"]: v for v in existing.get("verdicts", [])}

    results = sorted(
        p for p in RESULTS_DIR.glob("*.json")
        if not p.name.startswith("_")
    )
    if not results:
        print("No result JSONs in eval_set/_results/", file=sys.stderr)
        return 1

    print(f"\nSpotcheck — {len(results)} results in {RESULTS_DIR}")
    print("Verdict legend: g=good, m=meh, b=bad. s=skip, q=quit.")
    print("Saved after every entry. Re-run with --resume to continue later.\n")

    reviewed_now = 0
    for i, result_path in enumerate(results, 1):
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        pdf_name = payload.get("__eval__", {}).get("pdf") or (result_path.stem + ".pdf")

        if args.resume and pdf_name in verdicts:
            continue

        display(result_path, payload)
        if not args.no_open:
            pdf_path = find_pdf(pdf_name)
            if pdf_path:
                open_pdf(pdf_path)
            else:
                print(f"\n  (PDF not found for {pdf_name})")

        print(f"\n  [{i}/{len(results)}]")
        try:
            header = ask_choice(
                "Header  ",
                {"g": "good", "m": "meh", "b": "bad", "s": "skip", "q": "quit"},
            )
            if header == "q":
                break
            if header == "s":
                continue
            totals = ask_choice("Totals  ", {"g": "good", "m": "meh", "b": "bad"})
            notes = input("  Notes (opt): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nStopped.")
            break

        verdicts[pdf_name] = asdict(Verdict(
            pdf=pdf_name,
            header=header,
            totals=totals,
            notes=notes,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))
        VERDICTS_PATH.write_text(
            json.dumps({"verdicts": list(verdicts.values())}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        reviewed_now += 1

    n = len(verdicts)
    print("\n" + "=" * 64)
    print("SPOTCHECK SUMMARY")
    print("=" * 64)
    if n == 0:
        print("(no verdicts recorded)")
        return 0

    header_counts: Counter[str] = Counter(v["header"] for v in verdicts.values())
    totals_counts: Counter[str] = Counter(v["totals"] for v in verdicts.values())
    h_good = header_counts.get("g", 0) / n * 100
    t_good = totals_counts.get("g", 0) / n * 100

    print(f"Reviewed total: {n} / {len(results)}   (this session: {reviewed_now})")
    print(
        f"Header   good={header_counts.get('g', 0)}  "
        f"meh={header_counts.get('m', 0)}  "
        f"bad={header_counts.get('b', 0)}  →  {h_good:.1f}% good"
    )
    print(
        f"Totals   good={totals_counts.get('g', 0)}  "
        f"meh={totals_counts.get('m', 0)}  "
        f"bad={totals_counts.get('b', 0)}  →  {t_good:.1f}% good"
    )
    print()
    print("Phase 1 gate (§15):")
    print(f"  ≥85% header   →  {'PASS' if h_good >= 85 else 'FAIL'}   (got {h_good:.1f}%)")
    print(f"  ≥90% totals   →  {'PASS' if t_good >= 90 else 'FAIL'}   (got {t_good:.1f}%)")

    bad = [v for v in verdicts.values() if v["header"] == "b" or v["totals"] == "b"]
    if bad:
        print(f"\nFailing ones ({len(bad)}):")
        for v in bad:
            tags = []
            if v["header"] == "b":
                tags.append("header")
            if v["totals"] == "b":
                tags.append("totals")
            note = f"  — {v['notes']}" if v["notes"] else ""
            print(f"  [{','.join(tags):<13}] {v['pdf']}{note}")

    print(f"\nVerdicts file: {VERDICTS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

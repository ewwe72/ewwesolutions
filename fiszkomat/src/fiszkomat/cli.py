"""fiszkomat CLI — PDF -> .apkg via chunked Haiku generation."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from .core import run


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows console needs the nudge for Polish chars
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="fiszkomat", description="Polish skrypt PDF -> Anki .apkg")
    parser.add_argument("pdf", type=Path, help="Path to the skrypt PDF")
    parser.add_argument("-o", "--out", type=Path, default=None, help="Output .apkg path (default: <pdf-stem>.apkg)")
    parser.add_argument("--chunk-pages", type=int, default=5, help="Pages per chunk (default 5)")
    parser.add_argument("--dry-run", action="store_true", help="Print chunk plan, no API call")
    parser.add_argument("--max-chunks", type=int, default=None, help="Cap number of chunks (cheap test)")
    parser.add_argument("--mode", choices=["simple", "detailed"], default="simple",
                        help="Card schema. 'simple' = 5 fields (kolokwium prep, default). "
                             "'detailed' = 7 fields incl. przeciwwskazania + działania niepożądane (egzamin).")
    parser.add_argument("--env", type=Path, default=Path(".env"),
                        help="Path to .env. Default: ./.env in cwd. Each project owns its own; "
                             "do NOT point this at another project's .env.")
    args = parser.parse_args(argv)

    if not args.pdf.exists():
        print(f"PDF not found: {args.pdf}", file=sys.stderr)
        return 2
    load_dotenv(dotenv_path=args.env, override=True)  # parent shell may have ANTHROPIC_API_KEY="" — override
    out = args.out or args.pdf.with_suffix(".apkg")

    stats = run(
        pdf_path=args.pdf,
        out_path=out,
        pages_per_chunk=args.chunk_pages,
        dry_run=args.dry_run,
        max_chunks=args.max_chunks,
        card_mode=args.mode,
    )
    print()
    print(f"=== fiszkomat run summary ===")
    print(f"pages:           {stats.pdf_pages}")
    print(f"chunks:          {stats.chunks}")
    print(f"cards raw:       {stats.cards_raw}")
    print(f"cards valid:     {stats.cards_valid}")
    print(f"cards rejected:  {stats.cards_rejected}")
    print(f"input tokens:    {stats.input_tokens}")
    print(f"output tokens:   {stats.output_tokens}")
    print(f"cache read:      {stats.cache_read_tokens}")
    print(f"cache write:     {stats.cache_creation_tokens}")
    print(f"API cost (USD):  ${stats.api_cost_usd:.4f}")
    print(f"wall seconds:    {stats.wall_seconds:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

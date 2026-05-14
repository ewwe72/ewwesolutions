"""Phase 1 extraction prototype.

Usage:
    python scripts/extract_prototype.py path/to/invoice.pdf
    python scripts/extract_prototype.py path/to/invoice.pdf --telemetry

Reads ANTHROPIC_API_KEY from invoice_idp/.env, calls the extractor with
Haiku-first → Sonnet routing, prints CanonicalInvoice JSON to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline.extraction.extractor import extract_from_pdf, get_extractor  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def load_env(path: Path) -> None:
    """Read KEY=VALUE pairs from .env and inject into os.environ.

    Overwrites empty shell vars so a stray `ANTHROPIC_API_KEY=` in the
    parent shell doesn't shadow the real key in .env.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not value:
            continue
        if not os.environ.get(key):
            os.environ[key] = value


def main(argv: list[str] | None = None) -> int:
    # Windows console defaults to cp1252; force UTF-8 so Polish chars print cleanly.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Extract one invoice PDF → CanonicalInvoice JSON on stdout."
    )
    parser.add_argument("pdf", type=Path, help="Path to invoice PDF")
    parser.add_argument(
        "--telemetry",
        action="store_true",
        help="Print routing + token telemetry to stderr",
    )
    args = parser.parse_args(argv)

    load_env(ENV_PATH)

    if not args.pdf.exists():
        print(f"ERROR: file not found: {args.pdf}", file=sys.stderr)
        return 1

    try:
        extractor = get_extractor()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    try:
        run = extract_from_pdf(args.pdf, extractor)
    except Exception as e:
        print(f"EXTRACTION FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if args.telemetry:
        sonnet_disp = "-" if run.sonnet_confidence is None else f"{run.sonnet_confidence:.2f}"
        print(
            f"[telemetry] file={args.pdf.name} path={run.path_taken} "
            f"haiku_conf={run.haiku_confidence:.2f} sonnet_conf={sonnet_disp} "
            f"tokens_in={run.total_input_tokens} tokens_out={run.total_output_tokens}",
            file=sys.stderr,
        )

    print(json.dumps(run.invoice.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

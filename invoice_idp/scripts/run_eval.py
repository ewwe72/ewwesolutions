"""Eval harness — run extraction over a sample of eval_set/ PDFs.

Per Phase 1 done-when (§15): measures Haiku-first hit rate, average
confidence, validation-warning distribution, and total cost across the
sample. Per-invoice JSON outputs land in eval_set/_results/ so the
operator can spot-check accuracy.

Usage:
    python scripts/run_eval.py --sample 20
    python scripts/run_eval.py --all
    python scripts/run_eval.py --limit 5 invoice1.pdf invoice2.pdf

Resumes automatically: any PDF whose JSON output already exists in
_results/ is skipped (re-run with --force to overwrite).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline.extraction.extractor import (  # noqa: E402
    HAIKU_MODEL,
    SONNET_MODEL,
    extract_from_pdf,
    get_extractor,
)

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
EVAL_DIR = ROOT / "eval_set"
RESULTS_DIR = EVAL_DIR / "_results"
SUMMARY_PATH = RESULTS_DIR / "_summary.json"

# Anthropic pricing (USD per 1M tokens). Update when prices change.
PRICING: dict[str, tuple[float, float]] = {
    HAIKU_MODEL: (1.0, 5.0),
    SONNET_MODEL: (3.0, 15.0),
}

SAMPLE_SEED = 42


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value and not os.environ.get(key):
            os.environ[key] = value


def cost_usd(input_tokens: int, output_tokens: int, model: str) -> float:
    in_rate, out_rate = PRICING.get(model, (0.0, 0.0))
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate


@dataclass
class FileResult:
    pdf: str
    ok: bool
    path_taken: str | None
    haiku_conf: float | None
    sonnet_conf: float | None
    overall_conf: float | None
    hard_warnings: int
    soft_warnings: int
    warnings: list[str]
    haiku_input_tokens: int
    haiku_output_tokens: int
    sonnet_input_tokens: int
    sonnet_output_tokens: int
    cost_usd: float
    elapsed_s: float
    error: str | None


def select_pdfs(args: argparse.Namespace) -> list[Path]:
    explicit = [Path(p) for p in args.pdfs]
    if explicit:
        return explicit
    pdfs = sorted(p for p in EVAL_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    if args.sample is not None:
        rng = random.Random(SAMPLE_SEED)
        return rng.sample(pdfs, min(args.sample, len(pdfs)))
    if args.limit is not None:
        return pdfs[: args.limit]
    return pdfs


def run_one(pdf: Path, extractor: Any, force: bool) -> FileResult:
    out_path = RESULTS_DIR / f"{pdf.stem}.json"
    if out_path.exists() and not force:
        cached = json.loads(out_path.read_text(encoding="utf-8"))
        return FileResult(**cached["__eval__"])

    started = time.time()
    try:
        run = extract_from_pdf(pdf, extractor)
    except Exception as e:
        return FileResult(
            pdf=pdf.name,
            ok=False,
            path_taken=None,
            haiku_conf=None,
            sonnet_conf=None,
            overall_conf=None,
            hard_warnings=0,
            soft_warnings=0,
            warnings=[],
            haiku_input_tokens=0,
            haiku_output_tokens=0,
            sonnet_input_tokens=0,
            sonnet_output_tokens=0,
            cost_usd=0.0,
            elapsed_s=time.time() - started,
            error=f"{type(e).__name__}: {e}",
        )

    invoice = run.invoice
    hard = [w for w in invoice.extraction_warnings if not w.startswith("(soft)")]
    soft = [w for w in invoice.extraction_warnings if w.startswith("(soft)")]
    cost = (
        cost_usd(run.haiku_input_tokens, run.haiku_output_tokens, HAIKU_MODEL)
        + cost_usd(run.sonnet_input_tokens, run.sonnet_output_tokens, SONNET_MODEL)
    )

    result = FileResult(
        pdf=pdf.name,
        ok=True,
        path_taken=run.path_taken,
        haiku_conf=run.haiku_confidence,
        sonnet_conf=run.sonnet_confidence,
        overall_conf=invoice.overall_confidence,
        hard_warnings=len(hard),
        soft_warnings=len(soft),
        warnings=invoice.extraction_warnings,
        haiku_input_tokens=run.haiku_input_tokens,
        haiku_output_tokens=run.haiku_output_tokens,
        sonnet_input_tokens=run.sonnet_input_tokens,
        sonnet_output_tokens=run.sonnet_output_tokens,
        cost_usd=cost,
        elapsed_s=time.time() - started,
        error=None,
    )

    payload: dict[str, Any] = invoice.model_dump(mode="json")
    payload["__eval__"] = asdict(result)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def summarise(results: list[FileResult]) -> dict[str, Any]:
    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    n = len(ok)

    haiku_only = sum(1 for r in ok if r.path_taken == "haiku-only")
    haiku_then_sonnet = sum(1 for r in ok if r.path_taken == "haiku-then-sonnet")
    hit_rate = haiku_only / n if n else 0.0

    avg_conf = sum(r.overall_conf or 0 for r in ok) / n if n else 0.0
    total_hard = sum(r.hard_warnings for r in ok)
    total_soft = sum(r.soft_warnings for r in ok)
    total_cost = sum(r.cost_usd for r in results)
    total_in = sum(r.haiku_input_tokens + r.sonnet_input_tokens for r in results)
    total_out = sum(r.haiku_output_tokens + r.sonnet_output_tokens for r in results)

    warning_kinds: Counter[str] = Counter()
    for r in ok:
        for w in r.warnings:
            head = w.split(":", 1)[0].strip().removeprefix("(soft) ")
            warning_kinds[head] += 1

    return {
        "total": len(results),
        "ok": n,
        "failed": len(failed),
        "haiku_only": haiku_only,
        "haiku_then_sonnet": haiku_then_sonnet,
        "haiku_hit_rate": round(hit_rate, 3),
        "avg_overall_confidence": round(avg_conf, 3),
        "total_hard_warnings": total_hard,
        "total_soft_warnings": total_soft,
        "warning_kinds": dict(warning_kinds.most_common()),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_cost_usd": round(total_cost, 4),
        "avg_cost_usd_per_invoice": round(total_cost / len(results), 4) if results else 0.0,
        "failures": [{"pdf": r.pdf, "error": r.error} for r in failed],
    }


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Phase 1 eval harness — run extraction across eval_set/.")
    parser.add_argument("pdfs", nargs="*", help="Explicit PDFs (overrides --sample/--limit/--all)")
    parser.add_argument("--sample", type=int, help="Random sample N from eval_set/")
    parser.add_argument("--limit", type=int, help="First N sorted PDFs from eval_set/")
    parser.add_argument("--all", action="store_true", help="Process every PDF in eval_set/")
    parser.add_argument("--force", action="store_true", help="Re-extract even if _results/<name>.json exists")
    args = parser.parse_args(argv)

    load_env(ENV_PATH)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    targets = select_pdfs(args)
    if not targets:
        print("No PDFs selected", file=sys.stderr)
        return 1

    try:
        extractor = get_extractor()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"Processing {len(targets)} PDFs...\n")

    results: list[FileResult] = []
    for i, pdf in enumerate(targets, 1):
        if not pdf.exists():
            results.append(FileResult(
                pdf=pdf.name, ok=False, path_taken=None, haiku_conf=None,
                sonnet_conf=None, overall_conf=None, hard_warnings=0,
                soft_warnings=0, warnings=[], haiku_input_tokens=0,
                haiku_output_tokens=0, sonnet_input_tokens=0,
                sonnet_output_tokens=0, cost_usd=0.0, elapsed_s=0.0,
                error="file not found",
            ))
            print(f"  [{i:>3}/{len(targets)}] {pdf.name}  MISSING")
            continue
        r = run_one(pdf, extractor, args.force)
        results.append(r)
        if r.ok:
            sonnet_disp = "-" if r.sonnet_conf is None else f"{r.sonnet_conf:.2f}"
            print(
                f"  [{i:>3}/{len(targets)}] {pdf.name[:48]:<48}  "
                f"{r.path_taken:<17}  h={r.haiku_conf:.2f} s={sonnet_disp}  "
                f"warn={r.hard_warnings}/{r.soft_warnings}  ${r.cost_usd:.4f}  "
                f"{r.elapsed_s:.1f}s"
            )
        else:
            print(f"  [{i:>3}/{len(targets)}] {pdf.name[:48]:<48}  FAILED  {r.error}")

    summary = summarise(results)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== Summary ===")
    print(f"Total: {summary['total']}   ok: {summary['ok']}   failed: {summary['failed']}")
    print(f"Haiku-only: {summary['haiku_only']}   Haiku→Sonnet: {summary['haiku_then_sonnet']}")
    print(f"Haiku hit rate: {summary['haiku_hit_rate'] * 100:.1f}%   "
          f"(§14 gate: ≥60% for healthy margins)")
    print(f"Avg overall confidence: {summary['avg_overall_confidence']:.3f}")
    print(f"Hard warnings: {summary['total_hard_warnings']}   "
          f"Soft warnings: {summary['total_soft_warnings']}")
    if summary["warning_kinds"]:
        print("Warning breakdown:")
        for kind, n in summary["warning_kinds"].items():
            print(f"  {n:>3}  {kind}")
    print(f"Total tokens: {summary['total_input_tokens']:,} in / {summary['total_output_tokens']:,} out")
    print(f"Total cost: ${summary['total_cost_usd']:.4f}   "
          f"avg ${summary['avg_cost_usd_per_invoice']:.4f}/invoice")
    if summary["failures"]:
        print(f"\n{len(summary['failures'])} failure(s):")
        for f in summary["failures"]:
            print(f"  {f['pdf']}: {f['error']}")
    print(f"\nResults: {RESULTS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

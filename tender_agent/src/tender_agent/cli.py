"""Tender Agent CLI — Phase 0 prototype.

Usage:

  # List recent IT-procurement announcements (CPV 72*) — last 7 days
  python -m tender_agent.cli fetch-it

  # Cache one specific announcement (by bzpNumber, e.g. '2026/BZP 00240644')
  python -m tender_agent.cli fetch '2026/BZP 00240644'

  # Generate Markdown draft for a cached announcement against the demo firm
  python -m tender_agent.cli draft '2026/BZP 00240644'

  # Custom firm profile JSON (path relative to repo root)
  python -m tender_agent.cli draft '2026/BZP 00240644' --firm path/to/firm.json

Outputs land in `_samples/<flat-id>/`:
  raw.json         — raw API response
  body.html        — HTML body separated for grep / inspection
  parsed.json      — TenderAnnouncement (pydantic dump)
  draft.md         — concatenated A+B+C+D Markdown
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from .draft import DEFAULT_MODEL, draft_for_announcement, fmt_pl_datetime
from .fetch import _flat_id, fetch_full_record, iter_recent_it
from .models import (
    DraftBundle,
    FirmProfile,
    SiwzRequirements,
    TenderAnnouncement,
    VerificationReport,
)
from .parse import parse_record
from .verify import DEFAULT_MODEL as VERIFY_DEFAULT_MODEL
from .verify import verify_draft


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SAMPLE_DIR = REPO_ROOT / "tender_agent" / "_samples"
DEFAULT_LOG_DIR = REPO_ROOT / "tender_agent" / "_logs"
DEMO_FIRM_PATH = DEFAULT_SAMPLE_DIR / "curated" / "firm_demo.json"


console = Console()


def _load_firm(path: Path) -> FirmProfile:
    return FirmProfile.model_validate_json(path.read_text(encoding="utf-8"))


def cmd_fetch_it(args: argparse.Namespace) -> int:
    """List up to N IT-procurement announcements from the last `days`."""
    items = list(
        iter_recent_it(days=args.days, limit=args.limit, cpv_prefix=args.cpv_prefix)
    )
    if not items:
        console.print(
            f"[yellow]No CPV {args.cpv_prefix}* announcements in the last "
            f"{args.days} days.[/yellow]"
        )
        return 0

    table = Table(title=f"IT-procurement (CPV {args.cpv_prefix}*) — last {args.days}d")
    table.add_column("BZP")
    table.add_column("Published")
    table.add_column("Type")
    table.add_column("Object", overflow="fold")
    table.add_column("Org")
    for it in items:
        table.add_row(
            it.get("bzpNumber", ""),
            (it.get("publicationDate") or "")[:10],
            it.get("orderType", ""),
            (it.get("orderObject") or "")[:80],
            (it.get("organizationName") or "")[:40],
        )
    console.print(table)
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """Cache one announcement by bzpNumber. Prints summary."""
    sample_dir = Path(args.sample_dir)
    raw = fetch_full_record(
        args.bzp_number, sample_dir=sample_dir, date_window_days=args.window
    )
    ann = parse_record(raw, source_url="bzp-api-v1")
    target = sample_dir / _flat_id(args.bzp_number)
    (target / "parsed.json").write_text(
        ann.model_dump_json(indent=2), encoding="utf-8"
    )

    console.print(f"[green]Cached[/green] {args.bzp_number} → {target}/")
    console.print(f"  Tytuł: {ann.order_object}")
    console.print(f"  Zamawiający: {ann.organization_name} ({ann.organization_city})")
    if ann.cpv_main:
        console.print(f"  CPV: {ann.cpv_main.code} — {ann.cpv_main.label}")
    if ann.submitting_offers_date:
        console.print(
            f"  Termin ofert: {fmt_pl_datetime(ann.submitting_offers_date)}"
        )
    return 0


def cmd_draft(args: argparse.Namespace) -> int:
    """Generate a draft Markdown bundle for a cached announcement."""
    sample_dir = Path(args.sample_dir)
    log_dir = Path(args.log_dir)
    target = sample_dir / _flat_id(args.bzp_number)
    parsed_path = target / "parsed.json"
    if not parsed_path.exists():
        # Auto-fetch first.
        console.print(f"[yellow]Not cached, fetching first...[/yellow]")
        cmd_fetch(args)
    ann = TenderAnnouncement.model_validate_json(
        parsed_path.read_text(encoding="utf-8")
    )

    firm_path = Path(args.firm) if args.firm else DEMO_FIRM_PATH
    if not firm_path.exists():
        console.print(f"[red]Firm profile not found:[/red] {firm_path}")
        return 2
    firm = _load_firm(firm_path)

    siwz: SiwzRequirements | None = None
    if getattr(args, "siwz", None):
        if args.siwz == "auto":
            auto_path = target / "siwz_extracted.json"
            if auto_path.exists():
                siwz = SiwzRequirements.model_validate_json(
                    auto_path.read_text(encoding="utf-8")
                )
                console.print(f"[cyan]SIWZ context:[/cyan] {auto_path}")
            else:
                console.print(
                    f"[yellow]--siwz auto but {auto_path} not found — "
                    f"falling back to Phase 0 (no SIWZ context).[/yellow]"
                )
        else:
            siwz_path = Path(args.siwz)
            if not siwz_path.exists():
                console.print(f"[red]SIWZ JSON not found:[/red] {siwz_path}")
                return 2
            siwz = SiwzRequirements.model_validate_json(
                siwz_path.read_text(encoding="utf-8")
            )
            console.print(f"[cyan]SIWZ context:[/cyan] {siwz_path}")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Fall back to project .env, then to invoice_idp/.env (Phase 0 shares
        # the key — Anthropic direct API call, no Bedrock here yet).
        for env_path in [
            REPO_ROOT / "tender_agent" / ".env",
            REPO_ROOT / "invoice_idp" / ".env",
        ]:
            if not env_path.exists():
                continue
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
            if api_key:
                break
    if not api_key:
        console.print(
            "[red]No ANTHROPIC_API_KEY in env, tender_agent/.env, or invoice_idp/.env[/red]"
        )
        return 3

    draft_path = target / "draft.md"

    no_verify = bool(getattr(args, "no_verify", False))
    max_retries = max(0, int(getattr(args, "max_draft_retries", 0)))
    # Retries only matter if we verify each attempt — without verification
    # there's no signal to retry against. CLI surfaces this explicitly so
    # operators don't burn money silently.
    if no_verify and max_retries > 0:
        console.print(
            "[yellow]--no-verify with --max-draft-retries > 0 has no effect; "
            "verifier is the retry signal.[/yellow]"
        )
        max_retries = 0

    attempts: list[tuple[DraftBundle, str, VerificationReport | None]] = []
    for attempt_ix in range(max_retries + 1):
        if attempt_ix == 0:
            console.print(
                f"[cyan]Drafting[/cyan] {args.bzp_number} with model={args.model}"
                f" for firm={firm.short_name}..."
            )
        else:
            console.print(
                f"[yellow]Retry {attempt_ix}/{max_retries}[/yellow] "
                f"— previous attempt failed verification."
            )
        bundle = draft_for_announcement(
            ann, firm, siwz=siwz, model=args.model, log_dir=log_dir, api_key=api_key
        )
        md = _render_draft_md(bundle, ann, firm, args.model)

        if no_verify:
            attempts.append((bundle, md, None))
            break

        # Write the candidate before verifying — the verifier's
        # `draft_path` field on the report points at the file under review,
        # and we need that path to make sense even on intermediate retries.
        draft_path.write_text(md, encoding="utf-8")
        report = verify_draft(
            draft_path=draft_path,
            bundle=bundle,
            ann=ann,
            firm=firm,
            siwz=siwz,
            model=args.verify_model,
            log_dir=log_dir,
            api_key=api_key,
            skip_llm=args.skip_llm_verify,
        )
        attempts.append((bundle, md, report))
        if report.passed:
            break

    # Pick the best candidate by (n_errors asc, n_warns asc, attempt_ix asc).
    # Ties prefer earlier attempts so the operator sees the same draft on
    # rerun when there's no improvement possible.
    def _score(item: tuple[DraftBundle, str, VerificationReport | None]) -> tuple[int, int]:
        _b, _m, rep = item
        if rep is None:
            return (0, 0)
        errors = sum(1 for f in rep.findings if f.severity == "error")
        warns = sum(1 for f in rep.findings if f.severity == "warn")
        return (errors, warns)

    best_ix = min(range(len(attempts)), key=lambda i: _score(attempts[i]))
    best_bundle, best_md, best_report = attempts[best_ix]

    draft_path.write_text(best_md, encoding="utf-8")
    if len(attempts) > 1:
        console.print(
            f"[green]Wrote[/green] {draft_path} "
            f"(attempt {best_ix + 1}/{len(attempts)} chosen — fewest verifier findings)"
        )
    else:
        console.print(f"[green]Wrote[/green] {draft_path}")

    if best_report is not None:
        report_path = target / "verification.json"
        report_path.write_text(best_report.model_dump_json(indent=2), encoding="utf-8")
        _print_verification_report(best_report, report_path)
        if not best_report.passed:
            return 4
    return 0


def _render_draft_md(
    bundle: DraftBundle,
    ann: TenderAnnouncement,
    firm: FirmProfile,
    model: str,
) -> str:
    """Render a DraftBundle into the final user-visible Markdown.

    Pulled out of `cmd_draft` so the retry loop can produce a candidate
    string per attempt without spilling rendering details across iterations.
    """
    deadline = (
        fmt_pl_datetime(ann.submitting_offers_date)
        if ann.submitting_offers_date else "nie podano"
    )
    return f"""# Szkice ofertowe dla ogłoszenia {ann.bzp_number}

- **Wykonawca (demo):** {firm.short_name} (NIP {firm.nip})
- **Zamawiający:** {ann.organization_name}
- **Przedmiot:** {ann.order_object}
- **Termin składania ofert:** {deadline}
- **Model:** {model}

---

## A. Oświadczenie o niepodleganiu wykluczeniu

{bundle.oswiadczenie_wykluczenie_md}

## B. JEDZ — Część I

{bundle.jedz_czesc_1_md}

## C. Szkic listu intencyjnego

{bundle.list_intencyjny_md}

## D. Uwagi szkicownika

{bundle.model_notes or "(brak uwag)"}

## E. JEDZ — Część II: Informacje dotyczące wykonawcy

{bundle.jedz_czesc_2_md or "(brak — drafter pominął sekcję)"}

## F. JEDZ — Część III: Powody wykluczenia

{bundle.jedz_czesc_3_md or "(brak — drafter pominął sekcję)"}

## G. JEDZ — Część IV: Kryteria kwalifikacji

{bundle.jedz_czesc_4_md or "(brak — drafter pominął sekcję)"}
"""


def _print_verification_report(
    report: VerificationReport, report_path: Path
) -> None:
    status_color = "green" if report.passed else "red"
    status = "PASS" if report.passed else "FAIL"
    errors = sum(1 for f in report.findings if f.severity == "error")
    warns = sum(1 for f in report.findings if f.severity == "warn")
    infos = sum(1 for f in report.findings if f.severity == "info")
    console.print(
        f"[{status_color}]Verifier {status}[/{status_color}]  "
        f"errors={errors} warns={warns} infos={infos}  "
        f"llm_cost=${report.llm_cost_usd:.4f}  → {report_path}"
    )
    for f in report.findings:
        color = {"error": "red", "warn": "yellow", "info": "cyan"}.get(f.severity, "white")
        excerpt = f.excerpt if len(f.excerpt) <= 110 else f.excerpt[:107] + "..."
        console.print(
            f"  [{color}]{f.severity:5}[/{color}] [{f.category}/{f.source}] {excerpt}"
        )
        console.print(f"        → {f.suggestion}")


def cmd_verify(args: argparse.Namespace) -> int:
    """Verify a (previously generated) draft against firm + announcement [+ SIWZ]."""
    sample_dir = Path(args.sample_dir)
    log_dir = Path(args.log_dir)
    target = sample_dir / _flat_id(args.bzp_number)

    draft_path = Path(args.draft_path) if args.draft_path else (target / "draft.md")
    if not draft_path.exists():
        console.print(f"[red]Draft not found:[/red] {draft_path}")
        return 2

    parsed_path = target / "parsed.json"
    if not parsed_path.exists():
        console.print(f"[red]Parsed announcement not found:[/red] {parsed_path}")
        return 2
    ann = TenderAnnouncement.model_validate_json(
        parsed_path.read_text(encoding="utf-8")
    )

    firm_path = Path(args.firm) if args.firm else DEMO_FIRM_PATH
    if not firm_path.exists():
        console.print(f"[red]Firm profile not found:[/red] {firm_path}")
        return 2
    firm = _load_firm(firm_path)

    siwz: SiwzRequirements | None = None
    if args.siwz:
        siwz_path = (
            target / "siwz_extracted.json" if args.siwz == "auto" else Path(args.siwz)
        )
        if siwz_path.exists():
            siwz = SiwzRequirements.model_validate_json(
                siwz_path.read_text(encoding="utf-8")
            )
            console.print(f"[cyan]SIWZ context:[/cyan] {siwz_path}")
        elif args.siwz != "auto":
            console.print(f"[red]SIWZ JSON not found:[/red] {siwz_path}")
            return 2

    report = verify_draft(
        draft_path=draft_path,
        bundle=None,
        ann=ann,
        firm=firm,
        siwz=siwz,
        model=args.model,
        log_dir=log_dir,
        skip_llm=args.skip_llm,
    )

    if args.out:
        report_path = Path(args.out)
    else:
        report_path = draft_path.with_suffix(".verify.json")
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    _print_verification_report(report, report_path)
    return 0 if report.passed else 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tender-agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("fetch-it", help="List recent IT-procurement announcements")
    p_list.add_argument("--days", type=int, default=7)
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--cpv-prefix", default="72")
    p_list.set_defaults(func=cmd_fetch_it)

    p_get = sub.add_parser("fetch", help="Cache one announcement by bzpNumber")
    p_get.add_argument("bzp_number")
    p_get.add_argument("--window", type=int, default=30,
                       help="Days back to search the BZP API (default 30).")
    p_get.add_argument("--sample-dir", default=str(DEFAULT_SAMPLE_DIR))
    p_get.set_defaults(func=cmd_fetch)

    p_dr = sub.add_parser("draft", help="Generate Markdown draft for one announcement")
    p_dr.add_argument("bzp_number")
    p_dr.add_argument("--firm", help="Path to firm profile JSON",
                      default=str(DEMO_FIRM_PATH))
    p_dr.add_argument("--model", default=DEFAULT_MODEL)
    p_dr.add_argument("--window", type=int, default=30)
    p_dr.add_argument("--sample-dir", default=str(DEFAULT_SAMPLE_DIR))
    p_dr.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    p_dr.add_argument(
        "--siwz",
        default=None,
        help=(
            "Path to siwz_extracted.json to inject SIWZ context into the draft, "
            "or 'auto' to load <sample-dir>/<flat-id>/siwz_extracted.json if present. "
            "Without this flag, Phase 0 behavior is preserved (no SIWZ context)."
        ),
    )
    p_dr.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip the post-draft verifier sub-agent (deterministic + LLM checks).",
    )
    p_dr.add_argument(
        "--skip-llm-verify",
        action="store_true",
        help="Run verifier in deterministic-only mode (no LLM call).",
    )
    p_dr.add_argument(
        "--verify-model",
        default=VERIFY_DEFAULT_MODEL,
        help=f"Anthropic model for the LLM verifier (default {VERIFY_DEFAULT_MODEL}).",
    )
    p_dr.add_argument(
        "--max-draft-retries",
        type=int,
        default=2,
        help=(
            "If the verifier reports errors, regenerate up to this many times "
            "and keep the attempt with the fewest findings. 0 = no retry. "
            "Default 2 (=3 total attempts). No-op when --no-verify is set."
        ),
    )
    p_dr.set_defaults(func=cmd_draft)

    p_v = sub.add_parser(
        "verify",
        help="Verify a previously generated draft against firm + announcement [+ SIWZ].",
    )
    p_v.add_argument("bzp_number")
    p_v.add_argument(
        "--draft-path",
        default=None,
        help="Override the draft file path (default: <sample-dir>/<flat-id>/draft.md).",
    )
    p_v.add_argument("--firm", help="Path to firm profile JSON", default=str(DEMO_FIRM_PATH))
    p_v.add_argument("--siwz", default=None, help="Path to SIWZ JSON, or 'auto'.")
    p_v.add_argument("--model", default=VERIFY_DEFAULT_MODEL)
    p_v.add_argument("--skip-llm", action="store_true")
    p_v.add_argument("--out", default=None, help="Where to write the report JSON.")
    p_v.add_argument("--sample-dir", default=str(DEFAULT_SAMPLE_DIR))
    p_v.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    p_v.set_defaults(func=cmd_verify)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    exit_code: int = args.func(args)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())


# Convenience exports for `[project.scripts]` in pyproject.toml.
def fetch() -> int:  # pragma: no cover
    return main(["fetch-it"] + sys.argv[1:])


def draft() -> int:  # pragma: no cover
    return main(["draft"] + sys.argv[1:])

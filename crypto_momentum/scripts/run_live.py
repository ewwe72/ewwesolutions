"""Discord-alerting wrapper around ``python -m src.live`` for the crypto bot.

Same architecture and rule structure as the stocks bot's wrapper.
Crypto-specific differences:
  * Reads ``logs/crypto-bot.log`` (not ``momentum-bot.log``).
  * Footer text identifies which book the alert is from.
  * Rebalance-executed alert says "weekly", not "monthly".
  * Halt-threshold descriptions use the crypto defaults (-50/-25)
    rather than the stocks defaults (-35/-15).
  * Run date anchored to UTC to match the live driver's date logic.

USAGE
=====
    python -m scripts.run_live --state state/momentum_state.json
    python -m scripts.run_live --state state/momentum_state.json --dry-run

The webhook URL comes from the ``BIGGA`` env var (set in this project's
``.env``, which is a different Alpaca paper account from the stocks bot).
If unset, the wrapper still runs the bot; alert is skipped with stderr.

The wrapper's own exit code is the subprocess's exit code, so an external
scheduler that watches exit codes keeps working even if Discord is down.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Final

from dotenv import load_dotenv

_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_DEFAULT_LOG_DIR: Final[Path] = _PROJECT_ROOT / "logs"
_LOG_FILE_NAME: Final[str] = "crypto-bot.log"
_WEBHOOK_ENV_VAR: Final[str] = "BIGGA"

_COLOR_ERROR: Final[int] = 0xE74C3C
_COLOR_WARNING: Final[int] = 0xE67E22
_COLOR_INFO_GOOD: Final[int] = 0x2ECC71
_COLOR_INFO_NEUTRAL: Final[int] = 0x3498DB


# --------------------------------------------------------------------------- #
# Run summary                                                                 #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RunSummary:
    """Distilled view of one wrapper invocation."""

    exit_code: int
    run_date: date
    mode: str
    halt_before: bool = False
    halt_after: bool = False
    rebalanced_today: bool = False
    is_reset_invocation: bool = False
    reconciliation_divergences: tuple[str, ...] = ()
    feed_health_reasons: tuple[str, ...] = ()
    feed_fresh_pct: float | None = None
    rebalance_n_buys: int = 0
    rebalance_n_sells: int = 0
    rebalance_n_targets: int = 0
    error_messages: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Alert decision                                                              #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class AlertDecision:
    title: str
    color: int
    description: str
    fields: tuple[tuple[str, str], ...] = ()


# --------------------------------------------------------------------------- #
# The rule                                                                    #
# --------------------------------------------------------------------------- #

def decide_alert(summary: RunSummary) -> AlertDecision | None:
    """Translate a RunSummary into an AlertDecision or None.

    Priority (highest first):
      1. exit_code != 0     -> red error alert
      2. halt activated     -> orange warning
      3. halt resumed       -> blue info
      4. rebalance executed -> green info
      5. otherwise          -> None
    """
    if summary.is_reset_invocation:
        return None

    common_fields: list[tuple[str, str]] = [
        ("Date", summary.run_date.isoformat()),
        ("Mode", summary.mode),
    ]

    if summary.exit_code == 2:
        divs = summary.reconciliation_divergences or ("(no divergence detail in log)",)
        return AlertDecision(
            title="RECONCILIATION FAILURE — crypto bot halted",
            color=_COLOR_ERROR,
            description=(
                "Broker positions do not match persisted state. The bot has "
                "refused to trade and exited without saving state. Investigate, "
                "then resolve by editing the state file or by running "
                "`--reset-from-broker --i-understand-this-overwrites-state` if "
                "the broker is the source of truth."
            ),
            fields=tuple(common_fields + [
                ("Exit code", "2"),
                ("Divergences", _code_block("\n".join(divs))),
            ]),
        )

    if summary.exit_code == 3:
        reasons = summary.feed_health_reasons or ("(no feed-health detail in log)",)
        feed_fields: list[tuple[str, str]] = [
            ("Exit code", "3"),
            ("Reasons", _code_block("\n".join(reasons))),
        ]
        if summary.feed_fresh_pct is not None:
            feed_fields.append(("Fresh %", f"{summary.feed_fresh_pct:.1%}"))
        return AlertDecision(
            title="DATA FEED UNHEALTHY — crypto rebalance skipped",
            color=_COLOR_ERROR,
            description=(
                "Pre-rebalance feed sanity gate failed. Daily housekeeping "
                "and state persistence happened normally; only the trading "
                "leg was skipped. Re-run with `--force-rebalance` once the "
                "feed has recovered, or wait for next week's regular fire."
            ),
            fields=tuple(common_fields + feed_fields),
        )

    if summary.exit_code == 4:
        return AlertDecision(
            title="--reset-from-broker invoked without confirmation",
            color=_COLOR_ERROR,
            description=(
                "Someone ran the recovery flag without the "
                "`--i-understand-this-overwrites-state` confirmation. State "
                "was NOT modified. If this was intended, re-run with the "
                "confirmation flag. If not, investigate who ran it."
            ),
            fields=tuple(common_fields + [("Exit code", "4")]),
        )

    if summary.exit_code != 0:
        errs = summary.error_messages or ("(no error detail in log)",)
        return AlertDecision(
            title=f"Crypto live driver failed (exit {summary.exit_code})",
            color=_COLOR_ERROR,
            description=(
                "Unexpected non-zero exit. Check the bot log for the failing "
                "event. State may or may not have been saved; reconcile "
                "before the next scheduled run."
            ),
            fields=tuple(common_fields + [
                ("Exit code", str(summary.exit_code)),
                ("Errors", _code_block("\n".join(errs[:5]))),
            ]),
        )

    # Exit 0 — state transitions matter, daily no-ops don't.

    if summary.halt_after and not summary.halt_before:
        return AlertDecision(
            title="DRAWDOWN HALT ACTIVATED — next rebalance at 50% sizing",
            color=_COLOR_WARNING,
            description=(
                "Account equity dropped to or below the configured halt "
                "threshold (default -50% from peak — crypto's threshold is "
                "looser than stocks' because normal-cycle DDs are larger). "
                "The next rebalance will size to 50%. This is a catastrophe "
                "backstop, not a stop-loss; the strategy continues otherwise."
            ),
            fields=tuple(common_fields),
        )

    if summary.halt_before and not summary.halt_after:
        return AlertDecision(
            title="Drawdown halt resumed — back to full sizing",
            color=_COLOR_INFO_NEUTRAL,
            description=(
                "Account equity has recovered above the resume threshold "
                "(default -25% from peak). The next rebalance will size to 100%."
            ),
            fields=tuple(common_fields),
        )

    if summary.rebalanced_today:
        return AlertDecision(
            title="Crypto rebalance executed",
            color=_COLOR_INFO_GOOD,
            description=(
                f"Weekly rebalance ran cleanly: "
                f"{summary.rebalance_n_targets} targets selected, "
                f"{summary.rebalance_n_buys} buys / "
                f"{summary.rebalance_n_sells} sells submitted."
            ),
            fields=tuple(common_fields + [
                ("Targets", str(summary.rebalance_n_targets)),
                ("Buys", str(summary.rebalance_n_buys)),
                ("Sells", str(summary.rebalance_n_sells)),
            ]),
        )

    return None


def _code_block(text: str, max_lines: int = 12, max_chars: int = 900) -> str:
    """Wrap text in a Discord code block, truncating to fit embed limits."""
    lines = text.splitlines() or [text]
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more)"]
    body = "\n".join(lines)
    if len(body) > max_chars:
        body = body[:max_chars] + "\n..."
    return f"```\n{body}\n```"


# --------------------------------------------------------------------------- #
# State + log parsing                                                         #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class _StateSnapshot:
    halt_active: bool
    last_rebalance_date: date | None


def _read_state_snapshot(state_path: Path) -> _StateSnapshot:
    if not state_path.exists():
        return _StateSnapshot(halt_active=False, last_rebalance_date=None)
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _StateSnapshot(halt_active=False, last_rebalance_date=None)
    raw_date = data.get("last_rebalance_date")
    parsed_date = date.fromisoformat(raw_date) if isinstance(raw_date, str) else None
    return _StateSnapshot(
        halt_active=bool(data.get("halt_active") or False),
        last_rebalance_date=parsed_date,
    )


def _read_recent_log_events(log_path: Path, run_date: date) -> list[dict[str, Any]]:
    """JSON log lines whose timestamp begins with ``run_date``."""
    if not log_path.exists():
        return []
    events: list[dict[str, Any]] = []
    date_prefix = run_date.isoformat()
    try:
        with log_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = rec.get("timestamp", "")
                if isinstance(ts, str) and ts.startswith(date_prefix):
                    events.append(rec)
    except OSError:
        return []
    return events


def _build_summary(
    *,
    exit_code: int,
    run_date: date,
    mode: str,
    before: _StateSnapshot,
    after: _StateSnapshot,
    events: list[dict[str, Any]],
    is_reset_invocation: bool,
) -> RunSummary:
    rebalanced_today = (
        after.last_rebalance_date == run_date
        and before.last_rebalance_date != run_date
    )

    recon_divs: tuple[str, ...] = ()
    feed_reasons: tuple[str, ...] = ()
    feed_fresh_pct: float | None = None
    n_buys = 0
    n_sells = 0
    n_targets = 0
    errors: list[str] = []

    for ev in events:
        et = ev.get("event_type")
        payload = ev.get("payload") or {}
        if et == "reconciliation_failed":
            recon_divs = tuple(payload.get("divergences") or ())
        elif et == "rebalance_aborted_feed_unhealthy":
            feed_reasons = tuple(payload.get("reasons") or ())
            raw_pct = payload.get("fresh_pct")
            if isinstance(raw_pct, (int, float)):
                feed_fresh_pct = float(raw_pct)
        elif et == "rebalance_planned":
            n_buys = int(payload.get("n_buys") or 0)
            n_sells = int(payload.get("n_sells") or 0)
            n_targets = int(payload.get("n_targets") or 0)
        if ev.get("levelname") == "ERROR":
            msg = ev.get("message") or et or "(unlabelled error)"
            errors.append(str(msg))

    return RunSummary(
        exit_code=exit_code,
        run_date=run_date,
        mode=mode,
        halt_before=before.halt_active,
        halt_after=after.halt_active,
        rebalanced_today=rebalanced_today,
        is_reset_invocation=is_reset_invocation,
        reconciliation_divergences=recon_divs,
        feed_health_reasons=feed_reasons,
        feed_fresh_pct=feed_fresh_pct,
        rebalance_n_buys=n_buys,
        rebalance_n_sells=n_sells,
        rebalance_n_targets=n_targets,
        error_messages=tuple(errors[-5:]),
    )


# --------------------------------------------------------------------------- #
# Discord transport                                                           #
# --------------------------------------------------------------------------- #

def build_discord_payload(decision: AlertDecision) -> dict[str, Any]:
    return {
        "embeds": [{
            "title": decision.title[:256],
            "color": decision.color,
            "description": decision.description[:4096],
            "fields": [
                {"name": name[:256], "value": value[:1024], "inline": False}
                for name, value in decision.fields
            ],
            "footer": {"text": "crypto-momentum alert"},
        }]
    }


def post_to_discord(
    webhook_url: str,
    decision: AlertDecision,
    *,
    timeout_seconds: float = 10.0,
) -> bool:
    """POST the alert; True on success. Never raises."""
    payload = build_discord_payload(decision)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            # Discord is fronted by Cloudflare which rejects default
            # Python-urllib UA with 403 / CF error 1010. Any plausible
            # UA passes the edge filter.
            "User-Agent": "crypto-momentum-bot/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            status = int(resp.status)
            return 200 <= status < 300
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        print(f"[run_live] webhook POST failed: {e}", file=sys.stderr)
        return False


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run crypto src.live and post alerts to Discord on notable events.",
    )
    parser.add_argument("--state", required=True, help="Path to JSON state file")
    parser.add_argument("--mode", default="paper")
    args, passthrough = parser.parse_known_args(argv)

    load_dotenv()
    state_path = Path(args.state)
    log_path = _DEFAULT_LOG_DIR / _LOG_FILE_NAME

    is_reset = "--reset-from-broker" in passthrough

    before = _read_state_snapshot(state_path)
    subprocess_args = [
        sys.executable, "-m", "src.live",
        "--state", args.state,
        "--mode", args.mode,
    ] + passthrough
    proc = subprocess.run(subprocess_args, cwd=_PROJECT_ROOT)
    after = _read_state_snapshot(state_path)

    # Anchor run_date to UTC, matching the live driver's `utc_now().date()`.
    # The stocks bot's wrapper uses `date.today()` (local date) because its
    # bot runs on the ET calendar; here we want both wrapper and bot to
    # agree on which day's log lines to read.
    run_date = datetime.now(tz=timezone.utc).date()
    events = _read_recent_log_events(log_path, run_date)
    summary = _build_summary(
        exit_code=proc.returncode,
        run_date=run_date,
        mode=args.mode,
        before=before, after=after,
        events=events,
        is_reset_invocation=is_reset,
    )

    decision = decide_alert(summary)
    if decision is not None:
        webhook = os.environ.get(_WEBHOOK_ENV_VAR)
        if not webhook:
            print(
                f"[run_live] {_WEBHOOK_ENV_VAR} env var not set — alert skipped. "
                f"Would have sent: {decision.title}",
                file=sys.stderr,
            )
        else:
            ok = post_to_discord(webhook, decision)
            if ok:
                print(f"[run_live] alert posted: {decision.title}", file=sys.stderr)

    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())

"""Live trading driver for the cross-sectional momentum strategy.

Run once per trading day at market open. Performs three things, in order:

  1. **Reconciliation.** Compare persisted holdings against Alpaca's
     authoritative position list. Any divergence => halt and exit. No
     silent auto-correction. The bot will not run again until the
     operator has investigated and either fixed the state file or used
     ``--reset-from-broker`` (which is deliberately not in this CLI yet —
     it must be a conscious manual step, not a one-flag escape hatch).

  2. **Daily housekeeping.** Pull current account equity from Alpaca,
     update the drawdown circuit-breaker state, persist. This runs on
     EVERY trading day, not just rebalance days, so the daily-sampled
     halt can catch intramonth troughs the same way the backtest does.

  3. **Monthly rebalance.** Only on the first trading day of the month
     (and only if today != last_rebalance_date — guards against same-day
     re-runs). Pulls universe bars from yfinance, computes the target
     portfolio using the SAME pure-function primitives the backtest uses,
     diffs against actual holdings, submits sells then buys, updates
     persisted state from real fill prices and quantities.

Invocation
==========
    python -m src.live --state state/momentum_state.json
    python -m src.live --state state/momentum_state.json --dry-run

The ``--dry-run`` flag plans the rebalance and logs what *would* happen
without submitting any orders. Useful for the first paper-trade run.

Failure modes that exit non-zero (so external schedulers see them)
==================================================================
  * Reconciliation divergence (positions don't match what we persisted)
  * Same-day re-run when a rebalance has already been logged for today
    (treated as success, exit 0 — the run is genuinely a no-op)
  * Broker reachability errors
  * State file corruption / schema mismatch
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Final, Sequence, cast

import pandas as pd
import yaml
from dotenv import load_dotenv

from .backtest import _load_data_yf
from .data import AlpacaClient
from .executor import execute_rebalance_orders, find_reconciliation_divergences
from .logger import configure_logging, log_event
from .state import Holding, LiveState, load_state, save_state
from .strategy import (
    TargetPosition,
    build_symbol_metrics,
    compute_rebalance_orders,
    equal_weight_targets,
    is_eligible,
    is_first_trading_day_of_month,
    select_top_n,
)
from .utils.sp500 import SP500_SYMBOLS
from .utils.time import et_now
from .utils.universe import is_trading_day, trading_days


CONFIG_PATH: Final[Path] = Path(__file__).resolve().parent.parent / "config.yaml"


def _load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return cast(dict[str, Any], yaml.safe_load(fh))


# --------------------------------------------------------------------------- #
# Drawdown state update (mirrors backtest's daily sampling)                   #
# --------------------------------------------------------------------------- #

def _update_drawdown_state(
    state: LiveState,
    current_equity: float,
    *,
    halt_threshold: float,
    resume_threshold: float,
) -> None:
    """Mirror of ``strategy.update_drawdown_state``, but operating directly on
    the persisted ``LiveState`` (no separate DrawdownState dataclass needed
    because the state file already carries peak_equity / halt_active).

    Kept here rather than imported because it mutates ``LiveState`` rather
    than ``DrawdownState`` — same semantics, different carrier.
    """
    if current_equity <= 0:
        return
    if current_equity > state.peak_equity:
        state.peak_equity = current_equity
    if state.peak_equity <= 0:
        return
    dd = current_equity / state.peak_equity - 1.0
    if not state.halt_active and dd <= halt_threshold:
        state.halt_active = True
    elif state.halt_active and dd >= resume_threshold:
        state.halt_active = False


# --------------------------------------------------------------------------- #
# Reconciliation                                                              #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ReconciliationResult:
    """Outcome of the start-of-run reconciliation check."""

    ok: bool
    diffs: tuple[str, ...]  # human-readable strings, one per divergence


def _reconcile(client: AlpacaClient, state: LiveState) -> ReconciliationResult:
    """Compare persisted holdings to Alpaca's positions.

    First-run case (empty state, no broker positions) is trivially ok.
    First-run with broker positions present is NOT ok — that's a sign the
    account was used for something else and the bot doesn't know about it.
    """
    broker = [(p.symbol, p.qty) for p in client.positions()]
    persisted = {sym: h.qty for sym, h in state.holdings.items()}
    diffs = find_reconciliation_divergences(persisted, broker)
    if not diffs:
        return ReconciliationResult(ok=True, diffs=())
    lines = tuple(
        f"  {d.symbol}: persisted={d.persisted_qty}, broker={d.broker_qty}"
        for d in diffs
    )
    return ReconciliationResult(ok=False, diffs=lines)


# --------------------------------------------------------------------------- #
# Data-feed health check                                                      #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FeedHealthResult:
    """Outcome of the pre-rebalance feed sanity gate.

    Pure-functional verdict on whether the loaded bar set is healthy enough
    to act on. Two independent checks:

      * Aggregate fresh-pct: the share of the requested universe with a
        last-bar dated within ``staleness_window_days`` of ``yesterday``.
        Catches "yfinance returned empty frames for a big chunk."
      * Canary check: at least one canary symbol must have a bar dated
        exactly ``yesterday``. Catches "feed is uniformly N days stale"
        which would otherwise sneak through the aggregate window.
    """

    ok: bool
    n_requested: int
    n_loaded: int               # symbols with any data at all
    n_fresh: int                # symbols with last bar >= fresh_cutoff
    fresh_pct: float
    canary_fresh: dict[str, bool]
    reasons: tuple[str, ...]    # human-readable; empty when ok=True


def _check_feed_health(
    *,
    daily_by_symbol: dict[str, pd.DataFrame],
    requested_symbols: Sequence[str],
    yesterday: pd.Timestamp,
    fresh_cutoff: pd.Timestamp,
    min_fresh_pct: float,
    canary_symbols: Sequence[str],
) -> FeedHealthResult:
    """Decide whether the loaded universe is safe to rebalance against.

    ``fresh_cutoff`` is the earliest last-bar date that still counts as
    fresh (caller computes it by walking back ``staleness_window_days``
    trading days from ``yesterday``).
    """
    n_requested = len(requested_symbols)
    n_loaded = 0
    n_fresh = 0
    for sym in requested_symbols:
        df = daily_by_symbol.get(sym)
        if df is None or df.empty:
            continue
        n_loaded += 1
        last_bar = df.index[-1]
        if last_bar >= fresh_cutoff:
            n_fresh += 1

    fresh_pct = n_fresh / n_requested if n_requested > 0 else 0.0

    canary_fresh: dict[str, bool] = {}
    for sym in canary_symbols:
        df = daily_by_symbol.get(sym)
        if df is None or df.empty:
            canary_fresh[sym] = False
            continue
        canary_fresh[sym] = bool(df.index[-1] == yesterday)

    reasons: list[str] = []
    if fresh_pct < min_fresh_pct:
        reasons.append(
            f"fresh_pct {fresh_pct:.2%} < min {min_fresh_pct:.2%} "
            f"({n_fresh}/{n_requested} symbols fresh; cutoff "
            f"{fresh_cutoff.date()})"
        )
    if canary_symbols and not any(canary_fresh.values()):
        missing = [s for s, fresh in canary_fresh.items() if not fresh]
        reasons.append(
            f"no canary symbol has a bar dated {yesterday.date()}: "
            f"{missing}"
        )

    return FeedHealthResult(
        ok=not reasons,
        n_requested=n_requested,
        n_loaded=n_loaded,
        n_fresh=n_fresh,
        fresh_pct=fresh_pct,
        canary_fresh=canary_fresh,
        reasons=tuple(reasons),
    )


# --------------------------------------------------------------------------- #
# Rebalance                                                                   #
# --------------------------------------------------------------------------- #

def _compute_targets(
    *,
    yesterday: pd.Timestamp,
    daily_by_symbol: dict[str, pd.DataFrame],
    cfg: dict[str, Any],
    equity: float,
    leverage_mult: float,
) -> list[TargetPosition]:
    """Pure-functional target computation. Reuses the same primitives the
    backtest uses, so live and backtest decisions are by-construction
    identical given the same input data and equity."""
    strat = cfg["strategy"]
    metrics = []
    for sym in SP500_SYMBOLS:
        df = daily_by_symbol.get(sym)
        if df is None or df.empty:
            continue
        metrics.append(build_symbol_metrics(
            sym, df, yesterday,
            momentum_lookback=int(strat["momentum_lookback"]),
            momentum_skip=int(strat["momentum_skip"]),
            adv_lookback=int(strat["adv_lookback"]),
        ))
    eligible = [
        m for m in metrics
        if is_eligible(
            m,
            min_days_history=int(strat["min_days_history"]),
            min_price=float(strat["min_price"]),
            min_adv_dollars=float(strat["min_adv_dollars"]),
        )
    ]
    selected = select_top_n(eligible, int(strat["n_positions"]))
    return equal_weight_targets(
        selected,
        equity=equity * leverage_mult,
        position_cap_pct=float(strat["position_cap_pct"]),
        cash_reserve_pct=float(strat["cash_reserve_pct"]),
    )


def _do_rebalance(
    *,
    state: LiveState,
    client: AlpacaClient,
    cfg: dict[str, Any],
    today: date,
    equity: float,
    logger: logging.Logger,
    dry_run: bool,
) -> bool:
    """Compute and (unless dry_run) execute the monthly rebalance.

    Mutates ``state.holdings`` from actual fills. Sets
    ``state.last_rebalance_date = today`` on success. Returns True if the
    rebalance proceeded (or was a clean no-op dry run), False if it was
    aborted before any orders could be placed — caller translates False
    into a non-zero process exit so an external scheduler alerts.
    """
    # Pull data through yesterday (no lookahead). 450-day buffer comfortably
    # covers the 252-day momentum lookback + 60-day ADV.
    bar_end = today - timedelta(days=1)
    bar_start = bar_end - timedelta(days=450)
    log_event(
        logger,
        event_type="rebalance_data_load_begin",
        payload={"start": bar_start.isoformat(), "end": bar_end.isoformat()},
    )
    daily_by_symbol = _load_data_yf(SP500_SYMBOLS, bar_start, bar_end)
    n_loaded = sum(1 for d in daily_by_symbol.values() if not d.empty)
    log_event(
        logger,
        event_type="rebalance_data_load_complete",
        payload={"symbols_loaded": n_loaded, "symbols_requested": len(SP500_SYMBOLS)},
    )

    # Build trading calendar so we can pick "yesterday" properly
    cal_dates = trading_days(bar_start, today)
    if not cal_dates:
        log_event(
            logger,
            event_type="rebalance_aborted_no_calendar",
            level=logging.ERROR,
        )
        return False
    calendar = pd.DatetimeIndex([pd.Timestamp(d) for d in cal_dates])
    today_ts = pd.Timestamp(today)
    prior = calendar[calendar < today_ts]
    if len(prior) == 0:
        log_event(logger, event_type="rebalance_aborted_no_prior_day", level=logging.ERROR)
        return False
    yesterday_ts = prior[-1]

    # Data-feed sanity gate. yfinance routinely returns empty frames or
    # stale bars for a slice of the universe; without this gate the bot
    # would rank on a partial / stale universe and trade.
    live_cfg = cfg.get("live") or {}
    staleness_window = int(live_cfg.get("staleness_window_days", 3))
    # fresh_cutoff = the earliest last-bar date that still counts as fresh.
    # Walking back ``staleness_window`` trading days from yesterday: if
    # window=3, cutoff = prior[-3] so bars from {yesterday, day-1, day-2}
    # all count as fresh.
    if len(prior) >= staleness_window:
        fresh_cutoff = prior[-staleness_window]
    else:
        fresh_cutoff = prior[0]
    health = _check_feed_health(
        daily_by_symbol=daily_by_symbol,
        requested_symbols=SP500_SYMBOLS,
        yesterday=yesterday_ts,
        fresh_cutoff=fresh_cutoff,
        min_fresh_pct=float(live_cfg.get("min_universe_fresh_pct", 0.85)),
        canary_symbols=tuple(live_cfg.get("canary_symbols", ["AAPL", "MSFT", "GOOGL"])),
    )
    if not health.ok:
        log_event(
            logger,
            event_type="rebalance_aborted_feed_unhealthy",
            payload={
                "n_loaded": health.n_loaded,
                "n_fresh": health.n_fresh,
                "n_requested": health.n_requested,
                "fresh_pct": round(health.fresh_pct, 4),
                "canary_fresh": health.canary_fresh,
                "reasons": list(health.reasons),
                "fresh_cutoff": fresh_cutoff.date().isoformat(),
                "yesterday": yesterday_ts.date().isoformat(),
            },
            level=logging.ERROR,
        )
        for line in health.reasons:
            print(f"FEED HEALTH FAILURE: {line}", file=sys.stderr)
        return False
    log_event(
        logger,
        event_type="feed_health_ok",
        payload={
            "n_fresh": health.n_fresh,
            "n_requested": health.n_requested,
            "fresh_pct": round(health.fresh_pct, 4),
        },
    )

    leverage_mult = 0.5 if state.halt_active else 1.0
    targets = _compute_targets(
        yesterday=yesterday_ts,
        daily_by_symbol=daily_by_symbol, cfg=cfg,
        equity=equity, leverage_mult=leverage_mult,
    )
    current_qty = {sym: h.qty for sym, h in state.holdings.items()}
    orders = compute_rebalance_orders(current_qty, targets)

    log_event(
        logger,
        event_type="rebalance_planned",
        payload={
            "n_targets": len(targets),
            "n_orders": len(orders),
            "n_buys": sum(1 for q in orders.values() if q > 0),
            "n_sells": sum(1 for q in orders.values() if q < 0),
            "leverage_mult": leverage_mult,
            "halt_active": state.halt_active,
        },
    )

    if dry_run:
        log_event(logger, event_type="dry_run_no_orders_submitted", payload={"orders": orders})
        return True

    fills = execute_rebalance_orders(client, orders, logger=logger)

    # Update persisted state from ACTUAL fills, not intended fills.
    # Sells reduce qty; buys add to qty and update avg cost.
    today_d = today
    for sym, fill in fills.items():
        if fill.filled_qty == 0:
            continue
        if fill.side == "sell":
            if sym in state.holdings:
                cur = state.holdings[sym]
                remaining = cur.qty - fill.filled_qty
                if remaining <= 0:
                    del state.holdings[sym]
                else:
                    state.holdings[sym] = Holding(
                        symbol=sym, qty=remaining,
                        avg_cost=cur.avg_cost, entry_date=cur.entry_date,
                    )
        elif fill.side == "buy":
            if sym in state.holdings:
                cur = state.holdings[sym]
                new_total = cur.qty + fill.filled_qty
                # Weighted-average cost basis
                new_cost = (
                    cur.avg_cost * cur.qty + fill.avg_fill_price * fill.filled_qty
                ) / new_total
                state.holdings[sym] = Holding(
                    symbol=sym, qty=new_total,
                    avg_cost=new_cost, entry_date=cur.entry_date,
                )
            else:
                state.holdings[sym] = Holding(
                    symbol=sym, qty=fill.filled_qty,
                    avg_cost=fill.avg_fill_price, entry_date=today_d,
                )

    state.last_rebalance_date = today_d
    return True


# --------------------------------------------------------------------------- #
# Broker-truth state recovery                                                 #
# --------------------------------------------------------------------------- #

def _reset_state_from_broker(
    *,
    state_path: Path,
    client: AlpacaClient,
    today: date,
    logger: logging.Logger,
) -> int:
    """Overwrite the persisted state's holdings with Alpaca's current positions.

    Recovery escape hatch for the case where reconciliation has failed and
    the operator has determined the broker is correct. Resets ``holdings``
    to broker truth using ``avg_entry_price`` as cost basis and ``today``
    as the entry date (since we have no record of when the real entry was).

    Idempotency anchors (``last_run_date``, ``last_rebalance_date``) and
    the drawdown circuit-breaker state (``peak_equity``, ``halt_active``)
    are LEFT UNCHANGED — those are not "what we own" and the broker can't
    tell us what they should be.
    """
    state = load_state(state_path)
    broker_positions = client.positions()
    old_holdings = dict(state.holdings)
    new_holdings = {
        p.symbol: Holding(
            symbol=p.symbol,
            qty=p.qty,
            avg_cost=p.avg_entry_price,
            entry_date=today,
        )
        for p in broker_positions
    }
    state.holdings = new_holdings
    save_state(state, state_path)

    log_event(
        logger,
        event_type="state_reset_from_broker",
        payload={
            "n_old": len(old_holdings),
            "n_new": len(new_holdings),
            "old_symbols": sorted(old_holdings.keys()),
            "new_symbols": sorted(new_holdings.keys()),
        },
        level=logging.WARNING,
    )
    print(
        f"State reset from broker: holdings {len(old_holdings)} -> "
        f"{len(new_holdings)} symbols. Entry dates set to {today.isoformat()}.",
        file=sys.stderr,
    )
    return 0


# --------------------------------------------------------------------------- #
# Main one-shot run                                                           #
# --------------------------------------------------------------------------- #

def run_once(
    *,
    state_path: Path,
    mode: str = "paper",
    dry_run: bool = False,
    force_rebalance: bool = False,
) -> int:
    """One day's worth of work. Returns the intended process exit code.

    ``force_rebalance`` is for operator use only: force the rebalance flow
    today regardless of the calendar (bypasses both the first-trading-day
    check and the already-rebalanced-today guard). Useful for starting
    paper-trading mid-month or recovering after manual state surgery.
    """
    load_dotenv()
    cfg = _load_config()

    logger = configure_logging(
        log_dir=Path(cfg["logging"]["log_dir"]),
        rotate_bytes=int(cfg["logging"]["rotate_bytes"]),
        rotate_backups=int(cfg["logging"]["rotate_backups"]),
        level=logging.INFO,
    )

    today = et_now().date()
    if not is_trading_day(today):
        log_event(logger, event_type="non_trading_day", payload={"date": today.isoformat()})
        return 0

    state = load_state(state_path)
    log_event(
        logger,
        event_type="run_start",
        payload={
            "today": today.isoformat(),
            "last_run_date": state.last_run_date.isoformat() if state.last_run_date else None,
            "last_rebalance_date": (
                state.last_rebalance_date.isoformat()
                if state.last_rebalance_date else None
            ),
            "halt_active": state.halt_active,
            "n_holdings": len(state.holdings),
            "dry_run": dry_run,
        },
    )

    client = AlpacaClient(mode=mode)
    account = client.account()

    # 1. Reconciliation — hard halt on divergence
    recon = _reconcile(client, state)
    if not recon.ok:
        log_event(
            logger,
            event_type="reconciliation_failed",
            payload={"divergences": list(recon.diffs)},
            level=logging.ERROR,
        )
        # Don't save state — leave it as-is for operator inspection
        print("RECONCILIATION FAILURE — refusing to trade. Divergences:", file=sys.stderr)
        for line in recon.diffs:
            print(line, file=sys.stderr)
        return 2

    # 2. Daily housekeeping (always)
    _update_drawdown_state(
        state, account.equity,
        halt_threshold=float(cfg["risk"]["drawdown_halt_threshold"]),
        resume_threshold=float(cfg["risk"]["drawdown_resume_threshold"]),
    )
    log_event(
        logger,
        event_type="daily_housekeeping",
        payload={
            "equity": account.equity,
            "peak_equity": state.peak_equity,
            "drawdown": (
                account.equity / state.peak_equity - 1.0
                if state.peak_equity > 0 else 0.0
            ),
            "halt_active": state.halt_active,
        },
    )

    # 3. Monthly rebalance (only on first trading day of month, and not
    #    already done today)
    cal_dates = trading_days(today - timedelta(days=60), today + timedelta(days=10))
    calendar = pd.DatetimeIndex([pd.Timestamp(d) for d in cal_dates])
    is_rebal_day = is_first_trading_day_of_month(
        pd.Timestamp(today), trading_calendar=calendar
    )

    already_rebalanced_today = state.last_rebalance_date == today

    rebalance_ok = True
    # --force-rebalance bypasses both gates: the calendar check AND the
    # already-rebalanced-today guard. The flag is operator-use-only and
    # already requires explicit invocation, so the safety properties don't
    # rest on this conditional being narrow. Normal scheduled runs go
    # through the (is_rebal_day AND not already_rebalanced_today) path.
    if force_rebalance or (is_rebal_day and not already_rebalanced_today):
        rebalance_ok = _do_rebalance(
            state=state, client=client, cfg=cfg,
            today=today, equity=account.equity,
            logger=logger, dry_run=dry_run,
        )
    elif is_rebal_day and already_rebalanced_today:
        log_event(
            logger,
            event_type="rebalance_skipped_already_done_today",
        )
    else:
        log_event(logger, event_type="not_a_rebalance_day")

    state.last_run_date = today
    save_state(state, state_path)
    log_event(logger, event_type="run_complete")
    # Exit 3 if the rebalance aborted (e.g. unhealthy data feed). Daily
    # housekeeping and state persistence still happened — only the trading
    # leg was skipped — so an external scheduler can alert on the non-zero
    # exit without needing to clean anything up.
    return 0 if rebalance_ok else 3


def main() -> None:
    parser = argparse.ArgumentParser(description="Live momentum trader (one-shot per trading day)")
    parser.add_argument("--state", required=True, help="Path to JSON state file")
    parser.add_argument("--mode", choices=["paper"], default="paper")
    parser.add_argument("--dry-run", action="store_true",
                        help="Plan and log the rebalance but submit no orders")
    parser.add_argument("--force-rebalance", action="store_true",
                        help="Force the rebalance flow today regardless of the calendar. "
                             "Bypasses both the 'is first trading day of month' check and "
                             "the 'already rebalanced today' guard. Operator use only — "
                             "useful for starting paper-trading mid-month or recovering "
                             "after manual state surgery.")
    parser.add_argument(
        "--reset-from-broker", action="store_true",
        help="Overwrite persisted holdings with Alpaca's current positions and exit. "
             "Recovery escape hatch for reconciliation failures. Requires "
             "--i-understand-this-overwrites-state to actually do anything. Does NOT "
             "run a rebalance, reconciliation, or housekeeping; just rewrites holdings.",
    )
    parser.add_argument(
        "--i-understand-this-overwrites-state", action="store_true",
        help="Confirmation flag required by --reset-from-broker. Without it, "
             "--reset-from-broker exits non-zero without touching state.",
    )
    args = parser.parse_args()

    if args.reset_from_broker:
        if not args.i_understand_this_overwrites_state:
            print(
                "--reset-from-broker requires --i-understand-this-overwrites-state. "
                "This overwrites persisted holdings; cost-basis and entry-date "
                "history will be lost.",
                file=sys.stderr,
            )
            sys.exit(4)
        load_dotenv()
        cfg = _load_config()
        logger = configure_logging(
            log_dir=Path(cfg["logging"]["log_dir"]),
            rotate_bytes=int(cfg["logging"]["rotate_bytes"]),
            rotate_backups=int(cfg["logging"]["rotate_backups"]),
            level=logging.INFO,
        )
        client = AlpacaClient(mode=args.mode)
        sys.exit(_reset_state_from_broker(
            state_path=Path(args.state),
            client=client,
            today=et_now().date(),
            logger=logger,
        ))

    sys.exit(run_once(
        state_path=Path(args.state),
        mode=args.mode,
        dry_run=args.dry_run,
        force_rebalance=args.force_rebalance,
    ))


if __name__ == "__main__":
    main()

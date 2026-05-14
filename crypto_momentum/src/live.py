"""Live trading driver for the crypto momentum strategy.

Daily one-shot, exactly like the stocks bot, with three crypto-specific
adaptations:

  * **No trading calendar.** Markets are 24/7; every calendar day is a
    candidate run day. There is no "skip weekends/holidays" gate.
  * **Weekly rebalance trigger.** Rebalance only when today's weekday
    matches the configured target weekday (default Monday) and at least
    6 days have passed since the last rebalance.
  * **Crypto-class reconciliation.** When comparing persisted state to
    broker positions, filter the broker side to ``asset_class == "crypto"``
    so any (unexpected) non-crypto positions in the account don't blow
    up the reconcile.

Same safety properties as the stocks bot: reconciliation divergence is
a hard halt (exit 2), feed unhealthy is exit 3, misuse is exit 4.
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
    is_rebalance_day,
    select_top_n,
)
from .utils.crypto_universe import ALPACA_SYMBOLS
from .utils.time import utc_now


CONFIG_PATH: Final[Path] = Path(__file__).resolve().parent.parent / "config.yaml"


def _load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return cast(dict[str, Any], yaml.safe_load(fh))


# --------------------------------------------------------------------------- #
# Drawdown                                                                    #
# --------------------------------------------------------------------------- #

def _update_drawdown_state(
    state: LiveState,
    current_equity: float,
    *,
    halt_threshold: float,
    resume_threshold: float,
) -> None:
    """Mirror of strategy.update_drawdown_state operating directly on LiveState."""
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
    ok: bool
    diffs: tuple[str, ...]


def _reconcile(client: AlpacaClient, state: LiveState) -> ReconciliationResult:
    """Compare persisted crypto holdings to Alpaca's crypto positions.

    Only crypto-class positions are considered. If the account contains
    non-crypto positions (it shouldn't on a dedicated paper account, but
    paranoia is cheap), they're ignored — the stocks bot would catch them.
    """
    broker_crypto = [
        (p.symbol, p.qty) for p in client.positions()
        if p.asset_class == "crypto"
    ]
    persisted = {sym: h.qty for sym, h in state.holdings.items()}
    diffs = find_reconciliation_divergences(persisted, broker_crypto)
    if not diffs:
        return ReconciliationResult(ok=True, diffs=())
    lines = tuple(
        f"  {d.symbol}: persisted={d.persisted_qty}, broker={d.broker_qty}"
        for d in diffs
    )
    return ReconciliationResult(ok=False, diffs=lines)


# --------------------------------------------------------------------------- #
# Data-feed health (mirror of stocks bot)                                     #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FeedHealthResult:
    ok: bool
    n_requested: int
    n_loaded: int
    n_fresh: int
    fresh_pct: float
    canary_fresh: dict[str, bool]
    reasons: tuple[str, ...]


def _check_feed_health(
    *,
    daily_by_symbol: dict[str, pd.DataFrame],
    requested_symbols: Sequence[str],
    yesterday: pd.Timestamp,
    fresh_cutoff: pd.Timestamp,
    min_fresh_pct: float,
    canary_symbols: Sequence[str],
) -> FeedHealthResult:
    """Crypto-tuned feed sanity gate. Same shape as the stocks bot's gate."""
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
            f"no canary symbol has a bar dated {yesterday.date()}: {missing}"
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
    """Pure-functional target computation. Same primitives as the backtest."""
    strat = cfg["strategy"]
    metrics = []
    for sym in ALPACA_SYMBOLS:
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
    """Compute and (unless dry_run) execute the weekly rebalance."""
    # Data window: 60-day buffer comfortably covers the 30-day momentum
    # lookback + 14-day ADV lookback.
    bar_end = today - timedelta(days=1)
    bar_start = bar_end - timedelta(days=90)
    log_event(
        logger,
        event_type="rebalance_data_load_begin",
        payload={"start": bar_start.isoformat(), "end": bar_end.isoformat()},
    )
    daily_by_symbol = _load_data_yf(ALPACA_SYMBOLS, bar_start, bar_end)
    n_loaded = sum(1 for d in daily_by_symbol.values() if not d.empty)
    log_event(
        logger,
        event_type="rebalance_data_load_complete",
        payload={"symbols_loaded": n_loaded, "symbols_requested": len(ALPACA_SYMBOLS)},
    )

    # Crypto trades 365 days/year — calendar is every day in window.
    cal_dates = pd.date_range(
        start=pd.Timestamp(bar_start),
        end=pd.Timestamp(today),
        freq="D",
    )
    calendar = pd.DatetimeIndex(cal_dates)
    today_ts = pd.Timestamp(today)
    prior = calendar[calendar < today_ts]
    if len(prior) == 0:
        log_event(logger, event_type="rebalance_aborted_no_prior_day", level=logging.ERROR)
        return False
    yesterday_ts = prior[-1]

    # Data-feed sanity gate
    live_cfg = cfg.get("live") or {}
    staleness_window = int(live_cfg.get("staleness_window_days", 3))
    if len(prior) >= staleness_window:
        fresh_cutoff = prior[-staleness_window]
    else:
        fresh_cutoff = prior[0]
    health = _check_feed_health(
        daily_by_symbol=daily_by_symbol,
        requested_symbols=ALPACA_SYMBOLS,
        yesterday=yesterday_ts,
        fresh_cutoff=fresh_cutoff,
        min_fresh_pct=float(live_cfg.get("min_universe_fresh_pct", 0.80)),
        canary_symbols=tuple(live_cfg.get("canary_symbols", ["BTC/USD", "ETH/USD", "SOL/USD"])),
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

    # Update persisted state from actual fills
    today_d = today
    for sym, fill in fills.items():
        if fill.filled_qty == 0:
            continue
        if fill.side == "sell":
            if sym in state.holdings:
                cur = state.holdings[sym]
                remaining = cur.qty - fill.filled_qty
                if remaining <= 1e-12:
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
    """Overwrite persisted holdings with Alpaca's crypto positions.

    Identical contract to the stocks bot's reset: rewrites holdings,
    preserves DD state and idempotency anchors. Filters to crypto-class
    positions only (a stock position in the account, if any, is ignored).
    """
    state = load_state(state_path)
    broker_positions = [
        p for p in client.positions() if p.asset_class == "crypto"
    ]
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
    """One day's work for the crypto bot. Returns the process exit code."""
    load_dotenv()
    cfg = _load_config()

    logger = configure_logging(
        log_dir=Path(cfg["logging"]["log_dir"]),
        rotate_bytes=int(cfg["logging"]["rotate_bytes"]),
        rotate_backups=int(cfg["logging"]["rotate_backups"]),
        level=logging.INFO,
    )

    # We anchor "today" to UTC so that the weekly rebalance trigger
    # (Monday) is stable across DST transitions in the host's timezone.
    # The host may run this at any local time on its calendar Monday;
    # what matters is that the bot's view of "what day is it" doesn't
    # flip on a DST boundary.
    today = utc_now().date()

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

    # 1. Reconciliation
    recon = _reconcile(client, state)
    if not recon.ok:
        log_event(
            logger,
            event_type="reconciliation_failed",
            payload={"divergences": list(recon.diffs)},
            level=logging.ERROR,
        )
        print("RECONCILIATION FAILURE — refusing to trade. Divergences:", file=sys.stderr)
        for line in recon.diffs:
            print(line, file=sys.stderr)
        return 2

    # 2. Daily housekeeping
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

    # 3. Weekly rebalance — gated on day-of-week AND min-days-between
    is_rebal_day = is_rebalance_day(
        today,
        target_weekday=int(cfg["strategy"]["rebalance_weekday"]),
        last_rebalance_date=state.last_rebalance_date,
        min_days_between=6,
    )
    already_rebalanced_today = state.last_rebalance_date == today

    rebalance_ok = True
    if force_rebalance or (is_rebal_day and not already_rebalanced_today):
        rebalance_ok = _do_rebalance(
            state=state, client=client, cfg=cfg,
            today=today, equity=account.equity,
            logger=logger, dry_run=dry_run,
        )
    elif is_rebal_day and already_rebalanced_today:
        log_event(logger, event_type="rebalance_skipped_already_done_today")
    else:
        log_event(logger, event_type="not_a_rebalance_day",
                  payload={"weekday": today.weekday(),
                           "target": int(cfg["strategy"]["rebalance_weekday"])})

    state.last_run_date = today
    save_state(state, state_path)
    log_event(logger, event_type="run_complete")
    return 0 if rebalance_ok else 3


def main() -> None:
    parser = argparse.ArgumentParser(description="Live crypto momentum trader (one-shot per day)")
    parser.add_argument("--state", required=True, help="Path to JSON state file")
    parser.add_argument("--mode", choices=["paper"], default="paper")
    parser.add_argument("--dry-run", action="store_true",
                        help="Plan and log the rebalance but submit no orders")
    parser.add_argument("--force-rebalance", action="store_true",
                        help="Force the rebalance flow today regardless of the day-of-week "
                             "calendar. Bypasses both the weekday check and the "
                             "already-rebalanced-today guard. Operator use only — useful "
                             "for starting paper-trading mid-week.")
    parser.add_argument(
        "--reset-from-broker", action="store_true",
        help="Overwrite persisted holdings with Alpaca's crypto positions and exit. "
             "Requires --i-understand-this-overwrites-state.",
    )
    parser.add_argument(
        "--i-understand-this-overwrites-state", action="store_true",
        help="Confirmation flag required by --reset-from-broker.",
    )
    args = parser.parse_args()

    if args.reset_from_broker:
        if not args.i_understand_this_overwrites_state:
            print(
                "--reset-from-broker requires --i-understand-this-overwrites-state.",
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
            today=utc_now().date(),
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

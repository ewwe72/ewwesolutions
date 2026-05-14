"""Daily-rebalance backtest engine for cross-sectional momentum.

ENGINE FLOW per trading day:
  1. If today is a rebalance day:
       a. Compute symbol_metrics for each universe member, using daily bars
          STRICTLY BEFORE today (no lookahead into today's data).
       b. Filter eligible names (history, price, liquidity).
       c. Rank by 12-1 momentum, select top N.
       d. Build target portfolio (equal-weight with per-name cap), using
          equity computed at yesterday's close (also no lookahead into
          today's open price).
       e. Apply leverage multiplier from drawdown state.
       f. Diff current vs target → orders.
       g. Execute orders at today's OPEN (plus slippage). Each sell records
          a Trade with the closed shares' P&L.
  2. Mark-to-market portfolio value using today's CLOSE.
  3. Record daily equity.

NO-LOOKAHEAD invariants enforced:
  - Signal at T uses bars indexed < T (strictly before).
  - Fill prices come from T's open (the price you could have actually filled).
  - Drawdown state uses equity through yesterday's close.
  - End-of-backtest liquidation uses the final day's close for marking,
    not future prices.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from dotenv import load_dotenv

from .strategy import (
    DrawdownState,
    Side,
    SymbolMetrics,
    TargetPosition,
    build_symbol_metrics,
    compute_rebalance_orders,
    equal_weight_targets,
    is_eligible,
    is_first_trading_day_of_month,
    select_top_n,
    update_drawdown_state,
)
from .utils.sp500 import SP500_SYMBOLS
from .utils.universe import trading_days


# --------------------------------------------------------------------------- #
# Records                                                                     #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Trade:
    """One closed (or partially closed) position."""

    symbol: str
    side: Side                  # always LONG for now
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float          # avg cost basis at the moment of exit
    exit_price: float           # fill price including slippage
    qty: int                    # shares closed in this leg
    pnl: float
    pct_return: float           # pnl / (entry_price * qty)
    holding_days: int


@dataclass(frozen=True)
class RebalanceEvent:
    date: pd.Timestamp
    n_eligible: int             # how many symbols passed the eligibility filter
    n_selected: int             # how many made the top-N cut
    n_orders: int               # how many actual buy/sell tickets
    turnover_dollars: float     # gross dollars traded (buys + sells)
    leverage_multiplier: float  # 1.0 normal, 0.5 if DD halt active
    equity_at_open: float       # portfolio value at yesterday's close


@dataclass
class _Position:
    """Engine-internal mutable position record (cost basis carried forward)."""

    symbol: str
    qty: int
    avg_cost: float
    entry_date: pd.Timestamp


# --------------------------------------------------------------------------- #
# Config                                                                      #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BacktestConfig:
    start_date: date
    end_date: date
    symbols: tuple[str, ...] = SP500_SYMBOLS
    initial_cash: float = 100_000.0

    # Signal
    momentum_lookback: int = 252
    momentum_skip: int = 21

    # Eligibility
    min_days_history: int = 252
    min_price: float = 5.0
    min_adv_dollars: float = 10_000_000.0
    adv_lookback: int = 60

    # Portfolio
    n_positions: int = 50
    position_cap_pct: float = 0.03
    cash_reserve_pct: float = 0.02

    # Risk — catastrophe backstop. See config.yaml for rationale on -35/-15
    # vs the original -25/-10 (the latter were calibrated for monthly halt
    # sampling and destroy alpha under daily sampling).
    drawdown_halt_threshold: float = -0.35
    drawdown_resume_threshold: float = -0.15

    # Costs
    slippage_bps: float = 5.0   # 5 bps per side


# --------------------------------------------------------------------------- #
# Slippage                                                                    #
# --------------------------------------------------------------------------- #

def _fill_buy(quote: float, slippage_bps: float) -> float:
    return quote * (1.0 + slippage_bps / 10_000.0)


def _fill_sell(quote: float, slippage_bps: float) -> float:
    return quote * (1.0 - slippage_bps / 10_000.0)


# --------------------------------------------------------------------------- #
# Per-day helpers                                                             #
# --------------------------------------------------------------------------- #

def _prior_trading_day(today: pd.Timestamp, calendar: pd.DatetimeIndex) -> pd.Timestamp | None:
    """The trading day immediately before ``today``, or None if first day."""
    prior = calendar[calendar < today]
    if len(prior) == 0:
        return None
    return prior[-1]


def _bar_field(
    daily_by_symbol: dict[str, pd.DataFrame],
    symbol: str,
    day: pd.Timestamp,
    field: str,
) -> float | None:
    """Read OHLCV field for a symbol on a given day; None if missing."""
    df = daily_by_symbol.get(symbol)
    if df is None or df.empty:
        return None
    if day not in df.index:
        return None
    return float(df.at[day, field])


def _mark_to_market(
    positions: dict[str, _Position],
    cash: float,
    day: pd.Timestamp,
    daily_by_symbol: dict[str, pd.DataFrame],
) -> float:
    """Portfolio value at the close of ``day``.

    Symbols without a close on ``day`` are valued at their avg_cost (a
    conservative, no-lookahead stand-in for a "last known price"). This
    rarely matters in practice because rebalances exit before delisting.
    """
    equity = cash
    for sym, pos in positions.items():
        px = _bar_field(daily_by_symbol, sym, day, "close")
        if px is None:
            equity += pos.qty * pos.avg_cost
        else:
            equity += pos.qty * px
    return equity


# --------------------------------------------------------------------------- #
# Rebalance execution                                                         #
# --------------------------------------------------------------------------- #

def _execute_rebalance(
    *,
    today: pd.Timestamp,
    yesterday: pd.Timestamp,
    cfg: BacktestConfig,
    daily_by_symbol: dict[str, pd.DataFrame],
    positions: dict[str, _Position],
    cash: float,
    leverage_mult: float,
) -> tuple[float, list[Trade], RebalanceEvent]:
    """Compute and execute the rebalance for ``today``.

    Returns ``(new_cash, trades_recorded, rebalance_event)``.

    Mutates ``positions`` in place.
    """
    # 1. Build metrics for each universe symbol AS OF YESTERDAY's close.
    metrics: list[SymbolMetrics] = []
    for sym in cfg.symbols:
        df = daily_by_symbol.get(sym)
        if df is None or df.empty:
            continue
        m = build_symbol_metrics(
            sym, df, yesterday,
            momentum_lookback=cfg.momentum_lookback,
            momentum_skip=cfg.momentum_skip,
            adv_lookback=cfg.adv_lookback,
        )
        metrics.append(m)

    # 2. Filter eligible.
    eligible = [
        m for m in metrics
        if is_eligible(
            m,
            min_days_history=cfg.min_days_history,
            min_price=cfg.min_price,
            min_adv_dollars=cfg.min_adv_dollars,
        )
    ]

    # 3. Top-N.
    selected = select_top_n(eligible, cfg.n_positions)

    # 4. Equity at yesterday's close, scaled by leverage multiplier.
    current_equity = _mark_to_market(positions, cash, yesterday, daily_by_symbol)
    effective_equity = current_equity * leverage_mult

    # 5. Build target portfolio.
    targets = equal_weight_targets(
        selected,
        equity=effective_equity,
        position_cap_pct=cfg.position_cap_pct,
        cash_reserve_pct=cfg.cash_reserve_pct,
    )

    # 6. Diff to orders.
    current_qty = {sym: p.qty for sym, p in positions.items()}
    orders = compute_rebalance_orders(current_qty, targets)

    # 7. Execute at today's open (with slippage).
    trades_recorded: list[Trade] = []
    turnover_dollars = 0.0
    new_cash = cash

    # SELLS first (to free up cash), then BUYS
    sell_orders = {s: q for s, q in orders.items() if q < 0}
    buy_orders = {s: q for s, q in orders.items() if q > 0}

    for sym, delta_neg in sell_orders.items():
        open_px = _bar_field(daily_by_symbol, sym, today, "open")
        if open_px is None:
            # No data for today — skip this leg; position stays open until
            # next rebalance with data.
            continue
        sell_qty = -delta_neg  # positive shares to sell
        fill_px = _fill_sell(open_px, cfg.slippage_bps)
        proceeds = fill_px * sell_qty
        new_cash += proceeds
        turnover_dollars += proceeds

        pos = positions[sym]
        pnl = (fill_px - pos.avg_cost) * sell_qty
        pct_return = (
            (fill_px - pos.avg_cost) / pos.avg_cost if pos.avg_cost > 0 else 0.0
        )
        holding_days = (today.date() - pos.entry_date.date()).days
        trades_recorded.append(Trade(
            symbol=sym, side=Side.LONG,
            entry_date=pos.entry_date, exit_date=today,
            entry_price=pos.avg_cost, exit_price=fill_px,
            qty=sell_qty, pnl=pnl, pct_return=pct_return,
            holding_days=holding_days,
        ))

        new_qty = pos.qty - sell_qty
        if new_qty <= 0:
            del positions[sym]
        else:
            positions[sym] = _Position(sym, new_qty, pos.avg_cost, pos.entry_date)

    for sym, delta in buy_orders.items():
        open_px = _bar_field(daily_by_symbol, sym, today, "open")
        if open_px is None:
            continue
        fill_px = _fill_buy(open_px, cfg.slippage_bps)
        cost = fill_px * delta
        if cost > new_cash:
            # Trim to what cash allows. Conservative; happens rarely with
            # vol-targeted sizing but possible at edge cases.
            affordable = int(new_cash / fill_px)
            if affordable <= 0:
                continue
            delta = affordable
            cost = fill_px * delta
        new_cash -= cost
        turnover_dollars += cost

        if sym in positions:
            pos = positions[sym]
            new_total = pos.qty + delta
            new_cost = (pos.avg_cost * pos.qty + fill_px * delta) / new_total
            positions[sym] = _Position(sym, new_total, new_cost, pos.entry_date)
        else:
            positions[sym] = _Position(sym, delta, fill_px, today)

    event = RebalanceEvent(
        date=today,
        n_eligible=len(eligible),
        n_selected=len(selected),
        n_orders=len(orders),
        turnover_dollars=turnover_dollars,
        leverage_multiplier=leverage_mult,
        equity_at_open=current_equity,
    )
    return new_cash, trades_recorded, event


# --------------------------------------------------------------------------- #
# Main engine                                                                 #
# --------------------------------------------------------------------------- #

def run_backtest(
    cfg: BacktestConfig,
    daily_by_symbol: dict[str, pd.DataFrame],
    trading_calendar: pd.DatetimeIndex,
) -> tuple[list[Trade], "pd.Series[float]", list[RebalanceEvent]]:
    """Run the momentum backtest.

    Returns (closed_trades, daily_equity_curve, rebalance_events).
    """
    # Restrict calendar to test window (with one prior day for "yesterday")
    start_ts = pd.Timestamp(cfg.start_date)
    end_ts = pd.Timestamp(cfg.end_date)
    test_days = trading_calendar[(trading_calendar >= start_ts) & (trading_calendar <= end_ts)]
    if len(test_days) == 0:
        return [], pd.Series(dtype=float, name="equity"), []

    cash = cfg.initial_cash
    positions: dict[str, _Position] = {}
    closed_trades: list[Trade] = []
    rebalance_events: list[RebalanceEvent] = []
    equity_history: dict[pd.Timestamp, float] = {}
    dd_state = DrawdownState()

    for today in test_days:
        yesterday = _prior_trading_day(today, trading_calendar)

        if (
            yesterday is not None
            and is_first_trading_day_of_month(today, trading_calendar=trading_calendar)
        ):
            # Read the halt state as of yesterday's end-of-day update (set at
            # the bottom of the previous iteration). No lookahead — the state
            # reflects DD through yesterday's close only.
            leverage_mult = 0.5 if dd_state.halt_active else 1.0
            cash, new_trades, event = _execute_rebalance(
                today=today, yesterday=yesterday, cfg=cfg,
                daily_by_symbol=daily_by_symbol,
                positions=positions, cash=cash,
                leverage_mult=leverage_mult,
            )
            closed_trades.extend(new_trades)
            rebalance_events.append(event)

        equity_history[today] = _mark_to_market(positions, cash, today, daily_by_symbol)
        # Update drawdown state daily. Sampling only at rebalances misses
        # intramonth troughs — e.g. the COVID-March crash, where the trough
        # bounced back above -25% before the next rebalance and the halt
        # never fired. Daily sampling catches those excursions and (with
        # hysteresis) keeps the halt active until DD genuinely recovers.
        update_drawdown_state(
            dd_state, equity_history[today],
            halt_threshold=cfg.drawdown_halt_threshold,
            resume_threshold=cfg.drawdown_resume_threshold,
        )

    # End-of-backtest liquidation at the final day's close
    if positions and len(test_days) > 0:
        final_day = test_days[-1]
        for sym, pos in list(positions.items()):
            close_px = _bar_field(daily_by_symbol, sym, final_day, "close")
            if close_px is None:
                continue
            fill_px = _fill_sell(close_px, cfg.slippage_bps)
            pnl = (fill_px - pos.avg_cost) * pos.qty
            pct_return = (
                (fill_px - pos.avg_cost) / pos.avg_cost if pos.avg_cost > 0 else 0.0
            )
            holding_days = (final_day.date() - pos.entry_date.date()).days
            closed_trades.append(Trade(
                symbol=sym, side=Side.LONG,
                entry_date=pos.entry_date, exit_date=final_day,
                entry_price=pos.avg_cost, exit_price=fill_px,
                qty=pos.qty, pnl=pnl, pct_return=pct_return,
                holding_days=holding_days,
            ))
            cash += fill_px * pos.qty
            del positions[sym]

    eq = pd.Series(equity_history).sort_index() if equity_history else pd.Series(dtype=float)
    eq.name = "equity"
    return closed_trades, eq, rebalance_events


# --------------------------------------------------------------------------- #
# Report                                                                      #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BacktestReport:
    total_return: float
    cagr: float
    sharpe: float
    sortino: float
    max_drawdown: float
    max_drawdown_days: int
    win_rate: float
    profit_factor: float
    avg_win_pct: float
    avg_loss_pct: float
    expectancy_pct: float
    trade_count: int
    rebalance_count: int
    avg_turnover_pct: float        # avg gross turnover per rebalance as % of equity
    avg_n_selected: float          # avg names actually selected per rebalance
    drawdown_halts: int            # number of rebalances during DD halt

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = dataclasses.asdict(self)
        return d


def compute_report(
    trades: list[Trade],
    equity_curve: "pd.Series[float]",
    rebalances: list[RebalanceEvent],
    *,
    initial_cash: float,
) -> BacktestReport:
    if equity_curve.empty:
        return BacktestReport(
            total_return=0.0, cagr=0.0, sharpe=0.0, sortino=0.0,
            max_drawdown=0.0, max_drawdown_days=0, win_rate=0.0,
            profit_factor=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
            expectancy_pct=0.0, trade_count=0, rebalance_count=0,
            avg_turnover_pct=0.0, avg_n_selected=0.0, drawdown_halts=0,
        )

    final = float(equity_curve.iloc[-1])
    total_return = final / initial_cash - 1.0

    days = (equity_curve.index[-1] - equity_curve.index[0]).days
    years = max(days / 365.25, 1 / 365.25)
    cagr = (final / initial_cash) ** (1 / years) - 1 if final > 0 else -1.0

    daily_rets = equity_curve.pct_change().dropna()
    sharpe = (
        float(np.sqrt(252) * daily_rets.mean() / daily_rets.std())
        if daily_rets.std() > 0 else 0.0
    )
    downside = daily_rets[daily_rets < 0]
    sortino = (
        float(np.sqrt(252) * daily_rets.mean() / downside.std())
        if len(downside) > 1 and downside.std() > 0 else 0.0
    )

    running_max = equity_curve.cummax()
    drawdown = equity_curve / running_max - 1.0
    max_dd = float(drawdown.min())
    underwater = (drawdown < 0).astype(int)
    if underwater.any():
        groups = (underwater != underwater.shift()).cumsum()
        run_lengths = underwater.groupby(groups).sum()
        max_dd_days = int(run_lengths.max())
    else:
        max_dd_days = 0

    if trades:
        rets = np.array([t.pct_return for t in trades])
        wins = rets[rets > 0]
        losses = rets[rets <= 0]
        win_rate = float(len(wins) / len(rets))
        gross_win = float(wins.sum())
        gross_loss = float(-losses.sum())
        profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
        avg_win_pct = float(wins.mean()) if len(wins) else 0.0
        avg_loss_pct = float(losses.mean()) if len(losses) else 0.0
        expectancy_pct = float(rets.mean())
    else:
        win_rate = profit_factor = avg_win_pct = avg_loss_pct = expectancy_pct = 0.0

    if rebalances:
        turnover_pcts = [
            r.turnover_dollars / r.equity_at_open if r.equity_at_open > 0 else 0.0
            for r in rebalances
        ]
        avg_turnover_pct = float(np.mean(turnover_pcts))
        avg_n_selected = float(np.mean([r.n_selected for r in rebalances]))
        drawdown_halts = sum(1 for r in rebalances if r.leverage_multiplier < 1.0)
    else:
        avg_turnover_pct = avg_n_selected = 0.0
        drawdown_halts = 0

    return BacktestReport(
        total_return=total_return, cagr=cagr, sharpe=sharpe, sortino=sortino,
        max_drawdown=max_dd, max_drawdown_days=max_dd_days,
        win_rate=win_rate, profit_factor=profit_factor,
        avg_win_pct=avg_win_pct, avg_loss_pct=avg_loss_pct,
        expectancy_pct=expectancy_pct,
        trade_count=len(trades), rebalance_count=len(rebalances),
        avg_turnover_pct=avg_turnover_pct, avg_n_selected=avg_n_selected,
        drawdown_halts=drawdown_halts,
    )


# --------------------------------------------------------------------------- #
# Reporting / files                                                           #
# --------------------------------------------------------------------------- #

def write_report(
    *,
    report: BacktestReport,
    trades: list[Trade],
    equity_curve: "pd.Series[float]",
    rebalances: list[RebalanceEvent],
    benchmark_equity: "pd.Series[float] | None" = None,
    out_dir: Path,
    label: str,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / f"{label}_metrics.json").open("w", encoding="utf-8") as fh:
        json.dump(report.to_dict(), fh, indent=2, default=str)

    pd.DataFrame([{
        "symbol": t.symbol, "side": t.side.value,
        "entry_date": t.entry_date.date(), "entry_price": t.entry_price,
        "exit_date": t.exit_date.date(), "exit_price": t.exit_price,
        "qty": t.qty, "pnl": t.pnl, "pct_return": t.pct_return,
        "holding_days": t.holding_days,
    } for t in trades]).to_csv(
        out_dir / f"{label}_trades.csv", index=False, encoding="utf-8",
    )

    equity_curve.to_csv(out_dir / f"{label}_equity.csv", header=["equity"], encoding="utf-8")

    pd.DataFrame([{
        "date": r.date.date(),
        "n_eligible": r.n_eligible,
        "n_selected": r.n_selected,
        "n_orders": r.n_orders,
        "turnover_dollars": r.turnover_dollars,
        "leverage_multiplier": r.leverage_multiplier,
        "equity_at_open": r.equity_at_open,
    } for r in rebalances]).to_csv(
        out_dir / f"{label}_rebalances.csv", index=False, encoding="utf-8",
    )

    if not equity_curve.empty:
        fig, ax = plt.subplots(figsize=(10, 5))
        normalized = equity_curve / equity_curve.iloc[0]
        ax.plot(equity_curve.index, normalized.values, label="Strategy", linewidth=1.5)
        if benchmark_equity is not None and not benchmark_equity.empty:
            bench_norm = benchmark_equity / benchmark_equity.iloc[0]
            ax.plot(benchmark_equity.index, bench_norm.values,
                    label="SPY (buy & hold)", linewidth=1.2, alpha=0.7)
        ax.set_title(f"Equity curve (normalized) - {label}")
        ax.set_ylabel("equity / initial")
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(out_dir / f"{label}_equity.png", dpi=120)
        plt.close(fig)

        # Drawdown plot
        running_max = equity_curve.cummax()
        dd: "pd.Series[float]" = (equity_curve / running_max - 1) * 100
        fig, ax = plt.subplots(figsize=(10, 3))
        ax.fill_between(dd.index, dd.values, 0, color="red", alpha=0.3)
        ax.set_title(f"Drawdown - {label}")
        ax.set_ylabel("drawdown (%)")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(out_dir / f"{label}_drawdown.png", dpi=120)
        plt.close(fig)

    if trades:
        pct_rets = np.array([t.pct_return for t in trades])
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.hist(pct_rets * 100, bins=50, edgecolor="black", linewidth=0.4)
        ax.axvline(0, color="red", linewidth=0.8)
        ax.set_xlabel("per-position return (%)")
        ax.set_title(f"Per-position return distribution - {label}")
        fig.tight_layout()
        fig.savefig(out_dir / f"{label}_returns.png", dpi=120)
        plt.close(fig)

    with (out_dir / f"{label}_report.md").open("w", encoding="utf-8") as fh:
        fh.write(_format_markdown_report(
            report, label=label, benchmark_equity=benchmark_equity,
        ))


def _benchmark_stats(
    benchmark_equity: "pd.Series[float] | None",
) -> tuple[float, float, float, float] | None:
    """Return (total_return, cagr, sharpe, max_dd) for the benchmark, or None
    if no benchmark equity is provided. Same formulas as ``compute_report``
    so the strategy/benchmark rows are directly comparable."""
    if benchmark_equity is None or benchmark_equity.empty:
        return None
    bench = benchmark_equity.dropna()
    if bench.empty:
        return None
    initial = float(bench.iloc[0])
    final = float(bench.iloc[-1])
    if initial <= 0:
        return None
    total_return = final / initial - 1.0
    days = (bench.index[-1] - bench.index[0]).days
    years = max(days / 365.25, 1 / 365.25)
    cagr = (final / initial) ** (1 / years) - 1 if final > 0 else -1.0
    rets = bench.pct_change().dropna()
    sharpe = (
        float(np.sqrt(252) * rets.mean() / rets.std())
        if rets.std() > 0 else 0.0
    )
    running_max = bench.cummax()
    max_dd = float((bench / running_max - 1.0).min())
    return total_return, cagr, sharpe, max_dd


def _format_markdown_report(
    report: BacktestReport,
    *,
    label: str,
    benchmark_equity: "pd.Series[float] | None" = None,
) -> str:
    r = report
    rows = [
        f"# Backtest Report - {label}",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Total return | {r.total_return:.2%} |",
        f"| CAGR | {r.cagr:.2%} |",
        f"| Sharpe | {r.sharpe:.2f} |",
        f"| Sortino | {r.sortino:.2f} |",
        f"| Max drawdown | {r.max_drawdown:.2%} |",
        f"| Max DD duration | {r.max_drawdown_days} days |",
        f"| Rebalances | {r.rebalance_count} |",
        f"| Trades (closed positions) | {r.trade_count} |",
        f"| Avg names selected | {r.avg_n_selected:.1f} |",
        f"| Avg turnover per rebalance | {r.avg_turnover_pct:.1%} |",
        f"| Drawdown halts | {r.drawdown_halts} |",
        f"| Win rate (closed positions) | {r.win_rate:.2%} |",
        f"| Profit factor | {r.profit_factor:.2f} |",
        f"| Avg winner | {r.avg_win_pct:.2%} |",
        f"| Avg loser | {r.avg_loss_pct:.2%} |",
        f"| Expectancy / position | {r.expectancy_pct:.2%} |",
    ]

    bench_stats = _benchmark_stats(benchmark_equity)
    if bench_stats is not None:
        b_return, b_cagr, b_sharpe, b_dd = bench_stats
        rows += [
            "",
            "## Benchmark (SPY buy-and-hold, same window)",
            "",
            "| Metric | Strategy | SPY | Δ |",
            "|---|---:|---:|---:|",
            f"| Total return | {r.total_return:.2%} | {b_return:.2%} | "
            f"{(r.total_return - b_return):+.2%} |",
            f"| CAGR | {r.cagr:.2%} | {b_cagr:.2%} | "
            f"{(r.cagr - b_cagr):+.2%} |",
            f"| Sharpe | {r.sharpe:.2f} | {b_sharpe:.2f} | "
            f"{(r.sharpe - b_sharpe):+.2f} |",
            f"| Max drawdown | {r.max_drawdown:.2%} | {b_dd:.2%} | "
            f"{(r.max_drawdown - b_dd):+.2%} |",
        ]

    rows += [
        "",
        "## Modelling notes",
        "",
        "- Universe: S&P 500 constituents as of 2026-01 (snapshot list).",
        "- **Survivorship bias warning:** the universe list is static; symbols",
        "  removed from the index historically are not in the universe. This",
        "  likely inflates measured returns by ~1-3%/year.",
        "- Signal: 12-1 momentum (price 21 days ago / price 252 days ago - 1).",
        "- Selection: top N by score, equal-weighted with per-name and",
        "  cash-reserve caps.",
        "- Rebalance: first trading day of each month.",
        "- Costs: 5 bps slippage per side, $0 commission (Alpaca reality).",
        "- Risk: drawdown halt reduces sizing to 50% when DD <= halt_threshold;",
        "  resumes full size when DD >= resume_threshold (hysteresis prevents",
        "  flip-flopping). See config.yaml for the exact values used.",
        "- Data feed: Yahoo Finance (yfinance), split/dividend-adjusted",
        "  (auto_adjust=True). Alpaca remains the broker for live trading.",
    ]
    return "\n".join(rows)


# --------------------------------------------------------------------------- #
# Benchmark (SPY buy-and-hold over the same window)                           #
# --------------------------------------------------------------------------- #

def compute_spy_benchmark(
    spy_bars: pd.DataFrame,
    *,
    start: date,
    end: date,
    initial_cash: float,
) -> "pd.Series[float]":
    """Buy-and-hold SPY equity curve for fair comparison."""
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    bars = spy_bars.loc[(spy_bars.index >= start_ts) & (spy_bars.index <= end_ts)]
    if bars.empty:
        return pd.Series(dtype=float, name="spy_equity")
    initial_price = float(bars.iloc[0]["close"])
    eq: "pd.Series[float]" = (bars["close"].astype(float) / initial_price) * initial_cash
    eq.name = "spy_equity"
    return eq


# --------------------------------------------------------------------------- #
# Data loading                                                                #
# --------------------------------------------------------------------------- #

def _load_data_yf(
    symbols: tuple[str, ...],
    start: date,
    end: date,
    *,
    history_buffer_days: int = 400,
    batch_size: int = 100,
) -> dict[str, pd.DataFrame]:
    """Pull daily OHLCV via yfinance.

    Alpaca's free-tier daily bars only backfill to roughly early 2016 for
    most of the S&P 500 universe, which makes a 2010-onward IS window
    impossible — symbols don't become eligible (252-day history requirement)
    until ~Feb 2017. yfinance gives full multi-decade history at no cost,
    which is what backtests need. Alpaca remains the live broker via
    ``data.AlpacaClient`` — only the backtest data loader moved.

    Returns ``dict[symbol -> DataFrame]`` with lowercase OHLCV columns and a
    naive (tz-stripped) DatetimeIndex. Prices are split/dividend-adjusted
    via ``auto_adjust=True``.
    """
    import logging

    import yfinance as yf

    # Silence yfinance's noisy "possibly delisted" warnings — recent IPOs
    # legitimately have no data before their listing date, and the loader
    # already handles empty DataFrames downstream.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    intra_start = (start - timedelta(days=history_buffer_days)).isoformat()
    intra_end = (end + timedelta(days=1)).isoformat()

    yf_to_orig = {s.replace(".", "-"): s for s in symbols}
    yf_symbols = list(yf_to_orig.keys())
    out: dict[str, pd.DataFrame] = {s: pd.DataFrame() for s in symbols}

    needed = ["open", "high", "low", "close", "volume"]

    for i in range(0, len(yf_symbols), batch_size):
        batch = yf_symbols[i:i + batch_size]
        df = yf.download(
            tickers=batch,
            start=intra_start,
            end=intra_end,
            auto_adjust=True,
            progress=False,
            threads=True,
            group_by="ticker",
            actions=False,
        )
        if df is None or df.empty:
            continue
        for ysym in batch:
            if isinstance(df.columns, pd.MultiIndex):
                if ysym not in df.columns.get_level_values(0):
                    continue
                sub = cast(pd.DataFrame, df[ysym]).copy()
            else:
                sub = df.copy()
            sub = sub.dropna(how="all")
            if sub.empty:
                continue
            sub.columns = pd.Index([str(c).lower() for c in sub.columns])
            if not all(c in sub.columns for c in needed):
                continue
            sub = sub[needed]
            if isinstance(sub.index, pd.DatetimeIndex) and sub.index.tz is not None:
                sub.index = sub.index.tz_localize(None)
            out[yf_to_orig[ysym]] = sub
    return out


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-sectional momentum backtest")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--report", default="reports", help="Output directory")
    parser.add_argument(
        "--walk-forward", action="store_true",
        help="Split into IS (2010-2018) / OOS (2019-2024) and write both reports.",
    )
    parser.add_argument(
        "--n-positions", type=int, default=50,
        help="Top-N names to hold (default: 50)",
    )
    parser.add_argument(
        "--position-cap", type=float, default=0.03,
        help="Max weight per name (default: 0.03 = 3%%)",
    )
    args = parser.parse_args()

    load_dotenv()
    out_dir = Path(args.report)
    out_dir.mkdir(parents=True, exist_ok=True)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    # Universe load: SP500 constituents + SPY for the benchmark overlay.
    # (SPY is an ETF, not an S&P 500 constituent, so it's not in SP500_SYMBOLS.)
    # Backtest data comes from yfinance — Alpaca's free tier doesn't backfill
    # daily bars far enough to support a pre-2016 IS window. Live trading
    # still goes through AlpacaClient.
    symbols_to_load = (
        SP500_SYMBOLS + ("SPY",) if "SPY" not in SP500_SYMBOLS else SP500_SYMBOLS
    )
    print(f"Loading data via yfinance for {len(symbols_to_load)} symbols from {start} to {end}…")
    print("(Chunked into batches of 100 symbols; takes a few minutes)")
    daily_by_symbol = _load_data_yf(symbols_to_load, start, end)

    # Trading calendar from the actual NYSE schedule (independent of which
    # symbols loaded successfully). The buffer matches _load_data's, so the
    # earliest rebalance day has enough prior trading days for the 12-month
    # momentum lookback.
    cal_dates = trading_days(start - timedelta(days=400), end)
    trading_calendar = pd.DatetimeIndex([pd.Timestamp(d) for d in cal_dates])

    spy_bars = daily_by_symbol.get("SPY", pd.DataFrame())
    if spy_bars.empty:
        print("WARNING: SPY data unavailable; benchmark overlay will be omitted.")

    n_loaded = sum(1 for d in daily_by_symbol.values() if not d.empty)
    print(f"Loaded {n_loaded} symbols across {len(trading_calendar)} trading days.")

    def _run(label: str, s: date, e: date) -> None:
        cfg = BacktestConfig(
            start_date=s, end_date=e, symbols=SP500_SYMBOLS,
            n_positions=args.n_positions,
            position_cap_pct=args.position_cap,
        )
        trades, eq, rebs = run_backtest(
            cfg, daily_by_symbol=daily_by_symbol, trading_calendar=trading_calendar,
        )
        report = compute_report(trades, eq, rebs, initial_cash=cfg.initial_cash)

        spy_bench = compute_spy_benchmark(spy_bars, start=s, end=e, initial_cash=cfg.initial_cash)

        write_report(
            report=report, trades=trades, equity_curve=eq, rebalances=rebs,
            benchmark_equity=spy_bench, out_dir=out_dir, label=label,
        )
        print(f"  {label}: {len(trades)} trades over {len(rebs)} rebalances, "
              f"return {report.total_return:.2%}, Sharpe {report.sharpe:.2f}, "
              f"MaxDD {report.max_drawdown:.2%}")

    if args.walk_forward:
        is_end = date(2018, 12, 31)
        oos_start = date(2019, 1, 1)
        if start <= is_end:
            _run("in_sample", start, min(is_end, end))
            print("Running out-of-sample…")
        if end >= oos_start:
            _run("out_of_sample", max(oos_start, start), end)

        try:
            with (out_dir / "in_sample_metrics.json").open(encoding="utf-8") as fh:
                m_is = json.load(fh)
            with (out_dir / "out_of_sample_metrics.json").open(encoding="utf-8") as fh:
                m_oos = json.load(fh)
            # Better overfit/leakage checks: use both Sharpe AND total return
            # and guard against degenerate cases (negative or zero IS Sharpe)
            is_sharpe = float(m_is["sharpe"])
            oos_sharpe = float(m_oos["sharpe"])
            if is_sharpe > 0.1:
                overfit = oos_sharpe < 0.5 * is_sharpe
                leakage = oos_sharpe > 1.3 * is_sharpe
            elif is_sharpe < -0.1:
                # IS was negative; OOS shouldn't be much better OR much worse
                overfit = False
                leakage = oos_sharpe > 0.5  # noticeably positive OOS from a losing IS is suspicious
            else:
                overfit = leakage = False  # both essentially zero

            with (out_dir / "walk_forward_summary.md").open("w", encoding="utf-8") as fh:
                fh.write("# Walk-Forward Summary\n\n")
                fh.write("|                | In-sample             | Out-of-sample             |\n")
                fh.write("|----------------|----------------------:|--------------------------:|\n")
                fh.write(f"| Sharpe         | {is_sharpe:.2f}               | {oos_sharpe:.2f}                   |\n")
                fh.write(f"| Total return   | {m_is['total_return']:.2%}         | {m_oos['total_return']:.2%}             |\n")
                fh.write(f"| Max drawdown   | {m_is['max_drawdown']:.2%}         | {m_oos['max_drawdown']:.2%}             |\n")
                fh.write(f"| Win rate       | {m_is['win_rate']:.2%}             | {m_oos['win_rate']:.2%}                 |\n")
                fh.write(f"| Trades         | {m_is['trade_count']}                  | {m_oos['trade_count']}                      |\n")
                fh.write(f"\n**Likely overfit:** {'yes' if overfit else 'no'}\n")
                fh.write(f"**Likely data leakage:** {'yes' if leakage else 'no'}\n")
                fh.write("\n*Note: overfit/leakage flags use heuristics that are unreliable when IS Sharpe is near zero. Read the underlying numbers, not just the flag.*\n")
        except FileNotFoundError:
            pass
    else:
        _run("backtest", start, end)

    print(f"Wrote reports to {out_dir}/")


if __name__ == "__main__":
    main()

"""Daily-rebalance backtest engine for cross-sectional crypto momentum.

Mirror of the stocks bot's engine, with crypto-specific differences:

  * **Trading calendar = every calendar day.** No NYSE schedule, no
    weekend exclusion, no holidays. Bars are pulled for all days.
  * **Float quantities** throughout — TargetPosition.target_qty, Position.qty,
    Trade.qty are all floats.
  * **Weekly rebalance** on the configured weekday rather than first trading
    day of month.
  * **Benchmark is BTC-USD buy-and-hold** rather than SPY.
  * **Sharpe annualises to sqrt(365)** rather than sqrt(252) — crypto
    has no closed days, so daily-return variance scales differently.

The no-lookahead contract is identical: signal at T uses bars indexed < T,
fills at T's open, drawdown sampled daily after marking the close.
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
    is_rebalance_day,
    select_top_n,
    update_drawdown_state,
)
from .utils.crypto_universe import ALPACA_TO_YFINANCE, ALPACA_SYMBOLS


# --------------------------------------------------------------------------- #
# Records                                                                     #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Trade:
    """One closed (or partially closed) position."""

    symbol: str
    side: Side                  # always LONG (crypto bot is long-only on spot)
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    qty: float                  # float for crypto
    pnl: float
    pct_return: float
    holding_days: int


@dataclass(frozen=True)
class RebalanceEvent:
    date: pd.Timestamp
    n_eligible: int
    n_selected: int
    n_orders: int
    turnover_dollars: float
    leverage_multiplier: float
    equity_at_open: float


@dataclass
class _Position:
    symbol: str
    qty: float
    avg_cost: float
    entry_date: pd.Timestamp


# --------------------------------------------------------------------------- #
# Config                                                                      #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BacktestConfig:
    start_date: date
    end_date: date
    symbols: tuple[str, ...] = ALPACA_SYMBOLS    # ALPACA_SYMBOLS used as keys
    initial_cash: float = 100_000.0

    # Signal
    momentum_lookback: int = 30
    momentum_skip: int = 0

    # Eligibility
    min_days_history: int = 30
    min_price: float = 0.01
    min_adv_dollars: float = 5_000_000.0
    adv_lookback: int = 14

    # Portfolio
    n_positions: int = 7
    position_cap_pct: float = 0.18
    cash_reserve_pct: float = 0.05

    # Schedule
    rebalance_weekday: int = 0   # Monday
    min_days_between_rebalances: int = 6

    # Risk
    drawdown_halt_threshold: float = -0.50
    drawdown_resume_threshold: float = -0.25

    # Costs
    slippage_bps: float = 25.0


# --------------------------------------------------------------------------- #
# Slippage                                                                    #
# --------------------------------------------------------------------------- #

def _fill_buy(quote: float, slippage_bps: float) -> float:
    return quote * (1.0 + slippage_bps / 10_000.0)


def _fill_sell(quote: float, slippage_bps: float) -> float:
    return quote * (1.0 - slippage_bps / 10_000.0)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _prior_day(today: pd.Timestamp, calendar: pd.DatetimeIndex) -> pd.Timestamp | None:
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
    """Compute and execute the rebalance for ``today``. Mutates positions."""
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

    eligible = [
        m for m in metrics
        if is_eligible(
            m,
            min_days_history=cfg.min_days_history,
            min_price=cfg.min_price,
            min_adv_dollars=cfg.min_adv_dollars,
        )
    ]

    selected = select_top_n(eligible, cfg.n_positions)

    current_equity = _mark_to_market(positions, cash, yesterday, daily_by_symbol)
    effective_equity = current_equity * leverage_mult

    targets = equal_weight_targets(
        selected,
        equity=effective_equity,
        position_cap_pct=cfg.position_cap_pct,
        cash_reserve_pct=cfg.cash_reserve_pct,
    )

    current_qty = {sym: p.qty for sym, p in positions.items()}
    orders = compute_rebalance_orders(current_qty, targets)

    trades_recorded: list[Trade] = []
    turnover_dollars = 0.0
    new_cash = cash

    sell_orders = {s: q for s, q in orders.items() if q < 0}
    buy_orders = {s: q for s, q in orders.items() if q > 0}

    for sym, delta_neg in sell_orders.items():
        open_px = _bar_field(daily_by_symbol, sym, today, "open")
        if open_px is None:
            continue
        sell_qty = -delta_neg
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
        if new_qty <= 1e-12:
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
            affordable_qty = new_cash / fill_px
            if affordable_qty <= 0:
                continue
            delta = affordable_qty
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
    """Run the crypto momentum backtest."""
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
    last_rebalance: date | None = None

    for today in test_days:
        yesterday = _prior_day(today, trading_calendar)
        today_d = today.date()

        if (
            yesterday is not None
            and is_rebalance_day(
                today_d,
                target_weekday=cfg.rebalance_weekday,
                last_rebalance_date=last_rebalance,
                min_days_between=cfg.min_days_between_rebalances,
            )
        ):
            leverage_mult = 0.5 if dd_state.halt_active else 1.0
            cash, new_trades, event = _execute_rebalance(
                today=today, yesterday=yesterday, cfg=cfg,
                daily_by_symbol=daily_by_symbol,
                positions=positions, cash=cash,
                leverage_mult=leverage_mult,
            )
            closed_trades.extend(new_trades)
            rebalance_events.append(event)
            last_rebalance = today_d

        equity_history[today] = _mark_to_market(positions, cash, today, daily_by_symbol)
        update_drawdown_state(
            dd_state, equity_history[today],
            halt_threshold=cfg.drawdown_halt_threshold,
            resume_threshold=cfg.drawdown_resume_threshold,
        )

    # End-of-backtest liquidation
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
    avg_turnover_pct: float
    avg_n_selected: float
    drawdown_halts: int

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = dataclasses.asdict(self)
        return d


# Crypto trades 365 days/year (not 252). Daily-return variance scales by
# sqrt(N) where N = trading days per year, so the Sharpe annualisation
# factor changes accordingly.
_ANNUALISATION_FACTOR: float = float(np.sqrt(365))


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
        float(_ANNUALISATION_FACTOR * daily_rets.mean() / daily_rets.std())
        if daily_rets.std() > 0 else 0.0
    )
    downside = daily_rets[daily_rets < 0]
    sortino = (
        float(_ANNUALISATION_FACTOR * daily_rets.mean() / downside.std())
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
# Benchmark (BTC buy-and-hold)                                                #
# --------------------------------------------------------------------------- #

def compute_btc_benchmark(
    btc_bars: pd.DataFrame,
    *,
    start: date,
    end: date,
    initial_cash: float,
) -> "pd.Series[float]":
    """Buy-and-hold BTC equity curve for fair comparison.

    BTC is the closest analogue to SPY for crypto: a single liquid asset
    that captures most of the asset class's beta. A market-cap-weighted
    crypto index would be theoretically purer but doesn't have a clean
    free data source.
    """
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    bars = btc_bars.loc[(btc_bars.index >= start_ts) & (btc_bars.index <= end_ts)]
    if bars.empty:
        return pd.Series(dtype=float, name="btc_equity")
    initial_price = float(bars.iloc[0]["close"])
    eq: "pd.Series[float]" = (bars["close"].astype(float) / initial_price) * initial_cash
    eq.name = "btc_equity"
    return eq


def _benchmark_stats(
    benchmark_equity: "pd.Series[float] | None",
) -> tuple[float, float, float, float] | None:
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
        float(_ANNUALISATION_FACTOR * rets.mean() / rets.std())
        if rets.std() > 0 else 0.0
    )
    running_max = bench.cummax()
    max_dd = float((bench / running_max - 1.0).min())
    return total_return, cagr, sharpe, max_dd


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
                    label="BTC (buy & hold)", linewidth=1.2, alpha=0.7)
        ax.set_title(f"Equity curve (normalized) - {label}")
        ax.set_ylabel("equity / initial")
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(out_dir / f"{label}_equity.png", dpi=120)
        plt.close(fig)

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


def _format_markdown_report(
    report: BacktestReport,
    *,
    label: str,
    benchmark_equity: "pd.Series[float] | None" = None,
) -> str:
    r = report
    rows = [
        f"# Crypto Momentum Backtest - {label}",
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
            "## Benchmark (BTC buy-and-hold, same window)",
            "",
            "| Metric | Strategy | BTC | Δ |",
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
        "- Universe: Alpaca-tradable USD crypto pairs (~15 names). Survivorship",
        "  bias applies: pairs that delisted (LUNA, FTT, etc.) are absent. Crypto",
        "  survivorship is harder to quantify than equities' but is non-trivial.",
        "- Signal: trailing 30-day return, no skip. Crypto literature finds the",
        "  edge on 1-4 week horizons rather than the 12-month horizon used for",
        "  equities.",
        "- Selection: top-N by score, equal-weighted with per-name cap and cash",
        "  reserve. Fractional sizing throughout (no share rounding).",
        "- Rebalance: weekly on the configured weekday (default Monday), with a",
        "  minimum-days-between guard to prevent DST-induced doubles.",
        "- Costs: 25 bps slippage per side. Crypto execution on Alpaca's venue",
        "  is materially worse than equity execution; this is a realistic estimate.",
        "- Sharpe annualises to sqrt(365), not sqrt(252) — crypto trades every day.",
        "- Risk: DD halt at -50%, resume at -25%. Looser than stocks (-35/-15)",
        "  because crypto's normal-cycle drawdowns are larger; tighter thresholds",
        "  would essentially live in halt-active mode through every winter.",
        "- Data feed: Yahoo Finance (yfinance) for crypto bars. Alpaca remains",
        "  the broker for live trading.",
    ]
    return "\n".join(rows)


# --------------------------------------------------------------------------- #
# Data loading                                                                #
# --------------------------------------------------------------------------- #

def _load_data_yf(
    symbols_alpaca: tuple[str, ...],
    start: date,
    end: date,
    *,
    history_buffer_days: int = 90,
) -> dict[str, pd.DataFrame]:
    """Pull daily OHLCV via yfinance for the crypto universe.

    ``symbols_alpaca`` is the Alpaca-format list (``BTC/USD`` etc.).
    The function translates to yfinance format for the download and
    returns a dict keyed by the *Alpaca* symbol — so downstream code
    can treat backtest data and live data with the same keys.
    """
    import logging
    import yfinance as yf

    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    intra_start = (start - timedelta(days=history_buffer_days)).isoformat()
    intra_end = (end + timedelta(days=1)).isoformat()

    yfinance_symbols = [ALPACA_TO_YFINANCE[s] for s in symbols_alpaca if s in ALPACA_TO_YFINANCE]
    yf_to_alpaca = {ALPACA_TO_YFINANCE[s]: s for s in symbols_alpaca if s in ALPACA_TO_YFINANCE}

    out: dict[str, pd.DataFrame] = {s: pd.DataFrame() for s in symbols_alpaca}
    needed = ["open", "high", "low", "close", "volume"]

    if not yfinance_symbols:
        return out

    df = yf.download(
        tickers=yfinance_symbols,
        start=intra_start,
        end=intra_end,
        auto_adjust=True,
        progress=False,
        threads=True,
        group_by="ticker",
        actions=False,
    )
    if df is None or df.empty:
        return out

    for ysym in yfinance_symbols:
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
        out[yf_to_alpaca[ysym]] = sub
    return out


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-sectional crypto momentum backtest")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--report", default="reports", help="Output directory")
    parser.add_argument(
        "--walk-forward", action="store_true",
        help="Split into IS (start-2022-12-31) / OOS (2023-01-01-end) and write both reports.",
    )
    parser.add_argument("--n-positions", type=int, default=7)
    parser.add_argument("--position-cap", type=float, default=0.18)
    args = parser.parse_args()

    load_dotenv()
    out_dir = Path(args.report)
    out_dir.mkdir(parents=True, exist_ok=True)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    print(f"Loading crypto data via yfinance for {len(ALPACA_SYMBOLS)} pairs "
          f"from {start} to {end}…")
    daily_by_symbol = _load_data_yf(ALPACA_SYMBOLS, start, end)

    # Crypto trades 365 days/year. Calendar = every day in window.
    cal_dates = pd.date_range(
        start=pd.Timestamp(start) - pd.Timedelta(days=90),
        end=pd.Timestamp(end),
        freq="D",
    )
    trading_calendar = pd.DatetimeIndex(cal_dates)

    btc_bars = daily_by_symbol.get("BTC/USD", pd.DataFrame())
    if btc_bars.empty:
        print("WARNING: BTC data unavailable; benchmark overlay will be omitted.")

    n_loaded = sum(1 for d in daily_by_symbol.values() if not d.empty)
    print(f"Loaded {n_loaded}/{len(ALPACA_SYMBOLS)} pairs across "
          f"{len(trading_calendar)} days.")

    def _run(label: str, s: date, e: date) -> None:
        cfg = BacktestConfig(
            start_date=s, end_date=e, symbols=ALPACA_SYMBOLS,
            n_positions=args.n_positions,
            position_cap_pct=args.position_cap,
        )
        trades, eq, rebs = run_backtest(
            cfg, daily_by_symbol=daily_by_symbol, trading_calendar=trading_calendar,
        )
        report = compute_report(trades, eq, rebs, initial_cash=cfg.initial_cash)
        btc_bench = compute_btc_benchmark(btc_bars, start=s, end=e, initial_cash=cfg.initial_cash)
        write_report(
            report=report, trades=trades, equity_curve=eq, rebalances=rebs,
            benchmark_equity=btc_bench, out_dir=out_dir, label=label,
        )
        print(f"  {label}: {len(trades)} trades over {len(rebs)} rebalances, "
              f"return {report.total_return:.2%}, Sharpe {report.sharpe:.2f}, "
              f"MaxDD {report.max_drawdown:.2%}")

    if args.walk_forward:
        is_end = date(2022, 12, 31)
        oos_start = date(2023, 1, 1)
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
            is_sharpe = float(m_is["sharpe"])
            oos_sharpe = float(m_oos["sharpe"])
            if is_sharpe > 0.1:
                overfit = oos_sharpe < 0.5 * is_sharpe
                leakage = oos_sharpe > 1.3 * is_sharpe
            elif is_sharpe < -0.1:
                overfit = False
                leakage = oos_sharpe > 0.5
            else:
                overfit = leakage = False

            with (out_dir / "walk_forward_summary.md").open("w", encoding="utf-8") as fh:
                fh.write("# Walk-Forward Summary (Crypto)\n\n")
                fh.write("|                | In-sample             | Out-of-sample             |\n")
                fh.write("|----------------|----------------------:|--------------------------:|\n")
                fh.write(f"| Sharpe         | {is_sharpe:.2f}               | {oos_sharpe:.2f}                   |\n")
                fh.write(f"| Total return   | {m_is['total_return']:.2%}         | {m_oos['total_return']:.2%}             |\n")
                fh.write(f"| Max drawdown   | {m_is['max_drawdown']:.2%}         | {m_oos['max_drawdown']:.2%}             |\n")
                fh.write(f"| Win rate       | {m_is['win_rate']:.2%}             | {m_oos['win_rate']:.2%}                 |\n")
                fh.write(f"| Trades         | {m_is['trade_count']}                  | {m_oos['trade_count']}                      |\n")
                fh.write(f"\n**Likely overfit:** {'yes' if overfit else 'no'}\n")
                fh.write(f"**Likely data leakage:** {'yes' if leakage else 'no'}\n")
                fh.write("\n*Heuristic flags are unreliable when IS Sharpe is near zero. Read the numbers, not the flag.*\n")
        except FileNotFoundError:
            pass
    else:
        _run("backtest", start, end)

    print(f"Wrote reports to {out_dir}/")


if __name__ == "__main__":
    main()

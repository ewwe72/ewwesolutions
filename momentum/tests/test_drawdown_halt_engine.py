"""Engine-level drawdown-halt behavior.

The pure-function semantics of `update_drawdown_state` are covered by
`test_strategy.py` / `test_risk.py`. This file covers the integration:
does the backtest engine actually catch an intramonth drawdown trough
and apply the halt at the next rebalance, even if the trough has
already bounced back above the trip threshold by then?

History: an earlier version sampled the halt state only at rebalances,
so the COVID-March-2020 crash (peak-to-trough -36%, then a 15-percent-
point bounce by April 1) never fired the halt. Daily sampling closes
that gap.
"""
from __future__ import annotations

from datetime import timedelta

import pandas as pd

from src.backtest import BacktestConfig, run_backtest


def _bars(closes: list[float], end_date: pd.Timestamp) -> pd.DataFrame:
    """OHLCV frame with close = open = high = low = supplied path."""
    idx = pd.bdate_range(end=end_date, periods=len(closes))
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes,
         "close": closes, "volume": [50_000_000.0] * len(closes)},
        index=idx,
    )


def test_intramonth_dd_trough_trips_halt_for_next_rebalance() -> None:
    """A V-shape mid-month must trip the halt even though the next rebalance
    only sees a -20% drawdown (above the -25% trip threshold).

    Scenario:
      - 1-stock universe, fully invested after first rebalance.
      - Day 1 of month 2 (the first rebalance in the test window): buy at $100.
      - Mid-month: price plunges from $100 to $50 (peak-to-trough -50%),
        then recovers to $80 by month-end.
      - Day 1 of month 3 (next rebalance): position is at $80, DD vs peak
        is only -20% — above the -25% trip threshold.
      - Without daily halt sampling, the halt never fires.
      - With daily sampling, the trough trips the halt at -25%, and
        hysteresis keeps it active (DD never recovers above -10% resume)
        through the second rebalance.
    """
    # Build a calendar that spans April 1 to May 1, 2024 — two clean
    # first-of-month rebalance days. April 1 = Mon, May 1 = Wed. 23 business
    # days inclusive.
    history_days = 260              # enough for 252-day momentum lookback
    trading_window_days = 23
    n = history_days + trading_window_days
    end = pd.Timestamp("2024-05-01")

    # Stable $100 for the history window
    flat_history = [100.0] * history_days
    # Trading-window path (23 days): rebalance-decline-recover-plateau-rebalance
    # Day 0  (Apr 1) = first rebalance day, buy at $100
    # Days 1-7  (Apr 2-10) = decline 100 → 50  (DD -50% from peak)
    # Days 8-15 (Apr 11-22) = recover 50 → 80  (DD -20%)
    # Days 16-22 (Apr 23 - May 1) = plateau at 80
    decline = [100 - (100 - 50) * (i + 1) / 7 for i in range(7)]   # 92.86 ... 50.0
    recover = [50 + (80 - 50) * (i + 1) / 8 for i in range(8)]     # 53.75 ... 80.0
    plateau = [80.0] * 7
    test_path = [100.0] + decline + recover + plateau              # 1+7+8+7 = 23
    assert len(test_path) == trading_window_days

    closes = flat_history + test_path
    assert len(closes) == n

    bars_by = {"STK": _bars(closes, end)}
    calendar = pd.bdate_range(end=end, periods=n)

    # Start the backtest from the first trading day of the month containing
    # day `history_days` (i.e. start of the trading window). That's the
    # first rebalance. The next rebalance is the first trading day of the
    # following month.
    first_rebal_idx = history_days
    first_rebal_date = calendar[first_rebal_idx].date()

    cfg = BacktestConfig(
        start_date=first_rebal_date,
        end_date=end.date(),
        symbols=("STK",),
        n_positions=1,
        position_cap_pct=1.0,           # allow 100% in one name
        cash_reserve_pct=0.0,
        min_days_history=252,
        min_adv_dollars=1_000_000.0,
        slippage_bps=0.0,
        initial_cash=100_000.0,
        drawdown_halt_threshold=-0.25,
        drawdown_resume_threshold=-0.10,
    )

    trades, equity, rebs = run_backtest(cfg, bars_by, calendar)

    # Need at least 2 rebalances to test the post-trough one
    assert len(rebs) >= 2, (
        f"Test setup didn't produce two rebalances; got {len(rebs)}. "
        f"Check that the trading window straddles a month boundary."
    )

    # First rebalance: halt not yet active, leverage = 1.0
    assert rebs[0].leverage_multiplier == 1.0, (
        f"First rebalance should run at full leverage; got {rebs[0].leverage_multiplier}"
    )

    # Equity curve should have hit DD <= -25% during the test window
    peak = equity.cummax()
    dd = equity / peak - 1.0
    assert dd.min() <= -0.25, (
        f"Synthetic V-shape didn't actually breach -25% DD; min DD was {dd.min():.2%}. "
        f"Adjust the price path."
    )

    # The actual assertion: second rebalance, despite seeing only ~-20% DD
    # on its own day, runs at HALF leverage because the halt tripped
    # intramonth and hysteresis kept it active.
    second = rebs[1]
    assert second.leverage_multiplier == 0.5, (
        f"Halt should be active at second rebalance after intramonth -50% trough.\n"
        f"  Got leverage_multiplier = {second.leverage_multiplier}\n"
        f"  Trough DD seen: {dd.min():.2%}\n"
        f"  This means the engine is only sampling halt state at rebalances "
        f"and is missing intramonth drawdowns — the very bug this guards against."
    )

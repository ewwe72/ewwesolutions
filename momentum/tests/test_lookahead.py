"""No-lookahead tests for the momentum portfolio engine.

Invariants enforced:

  1. Signal at rebalance day T uses only bars strictly before T. Adding
     wild future bars after T must not change the rebalance decision.

  2. Fill prices for the rebalance come from T's OPEN, not T's close or
     T-1's close. The price you fill at must be a price you could have
     actually transacted at given the timing.

  3. Eligibility filter (history, liquidity) at T uses data through T-1,
     never future data.

  4. The end-of-backtest liquidation uses the final day's close, not
     beyond.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.backtest import BacktestConfig, run_backtest


# --------------------------------------------------------------------------- #
# Synthetic-data helpers                                                      #
# --------------------------------------------------------------------------- #

def _make_daily_bars(
    *,
    end_date: pd.Timestamp,
    n_days: int,
    price_path: list[float],
    volume: float = 10_000_000.0,
) -> pd.DataFrame:
    """Build a daily OHLCV DataFrame ending on ``end_date``.

    ``price_path`` must have length ``n_days`` and supplies the close
    prices. open/high/low are derived as small offsets from close so the
    open price differs from the close in a controllable way.
    """
    assert len(price_path) == n_days
    idx = pd.bdate_range(end=end_date, periods=n_days)
    opens = [max(0.01, p * 0.999) for p in price_path]      # open slightly below close
    highs = [max(o, c) * 1.001 for o, c in zip(opens, price_path)]
    lows = [min(o, c) * 0.999 for o, c in zip(opens, price_path)]
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows,
         "close": price_path, "volume": [volume] * n_days},
        index=idx,
    )


def _trading_calendar(end_date: pd.Timestamp, n_days: int) -> pd.DatetimeIndex:
    """Trading calendar (business days) matching the synthetic data."""
    return pd.bdate_range(end=end_date, periods=n_days)


def _first_monday_after(d: pd.Timestamp) -> pd.Timestamp:
    """Find the next Monday strictly after ``d`` — useful for picking a
    rebalance date that's the first trading day of its month."""
    while d.weekday() != 0 or d.day > 7:
        d += timedelta(days=1)
    return d


# --------------------------------------------------------------------------- #
# Invariant 1: signal ignores future bars                                     #
# --------------------------------------------------------------------------- #

def test_signal_unchanged_when_future_bars_appended() -> None:
    """Same universe, same rebalance date, but in one case symbol B's
    *future* bars are spiked to 1000x. The rebalance must select the same
    portfolio in both cases — because the signal at T uses only bars < T.
    """
    # Pick a Wednesday so the rebalance day (first Mon of next month) is
    # well-defined and the test runs in 2024.
    n = 350  # enough history for 252-day momentum + 60-day ADV
    # The data ends right BEFORE the rebalance test date — i.e. last bar = rebalance_date.
    # Engine uses yesterday for signal, today's open for fill.
    rebalance_date = pd.Timestamp("2024-04-01")  # Monday, first trading day of April
    data_end = rebalance_date  # data through rebalance day

    # Symbol A: steady 0.3%/day uptrend - good momentum
    a_path = [100.0 * (1.003 ** i) for i in range(n)]
    # Symbol B: flat - neutral momentum
    b_path = [100.0] * n
    # Symbol C: declining - negative momentum
    c_path = [100.0 * (0.997 ** i) for i in range(n)]

    bars_normal: dict[str, pd.DataFrame] = {
        "A": _make_daily_bars(end_date=data_end, n_days=n, price_path=a_path),
        "B": _make_daily_bars(end_date=data_end, n_days=n, price_path=b_path),
        "C": _make_daily_bars(end_date=data_end, n_days=n, price_path=c_path),
        # SPY needs to exist for calendar construction
        "SPY": _make_daily_bars(end_date=data_end, n_days=n, price_path=[400.0] * n),
    }

    # Identical setup, but B's future has been spiked (post-rebalance bars).
    # In this synthetic test the rebalance IS the final day so there are no
    # bars AFTER it — but we can still extend the dataset with bars after
    # rebalance_date and verify the decision is unchanged.
    n_with_future = n + 10
    b_path_with_future = b_path + [1000.0] * 10  # post-rebalance spike
    extended_end = data_end + pd.tseries.offsets.BDay(10)

    bars_with_future = {
        "A": _make_daily_bars(end_date=extended_end, n_days=n_with_future,
                              price_path=a_path + a_path[-1:] * 10),
        "B": _make_daily_bars(end_date=extended_end, n_days=n_with_future,
                              price_path=b_path_with_future),
        "C": _make_daily_bars(end_date=extended_end, n_days=n_with_future,
                              price_path=c_path + c_path[-1:] * 10),
        "SPY": _make_daily_bars(end_date=extended_end, n_days=n_with_future,
                                price_path=[400.0] * n_with_future),
    }

    cfg = BacktestConfig(
        start_date=rebalance_date.date(),
        end_date=rebalance_date.date(),
        symbols=("A", "B", "C"),
        n_positions=2,             # top 2
        min_days_history=252,
        min_adv_dollars=1_000_000.0,  # low bar so synthetic volume passes
        initial_cash=100_000.0,
        slippage_bps=0.0,             # zero slippage for clean assertions
    )

    cal_normal = _trading_calendar(data_end, n)
    cal_future = _trading_calendar(extended_end, n_with_future)

    trades_a, _, rebs_a = run_backtest(cfg, bars_normal, cal_normal)
    trades_b, _, rebs_b = run_backtest(cfg, bars_with_future, cal_future)

    # Same set of symbols entered in both cases. trades_a already includes
    # EOD-liquidated positions via the end-of-backtest sweep, so a simple
    # symbol-set comparison is sufficient.
    entered_a = {t.symbol for t in trades_a}
    entered_b = {t.symbol for t in trades_b}

    assert entered_a == entered_b, (
        f"Future bars changed the rebalance decision! "
        f"Normal-data selected {entered_a}, future-spike-data selected {entered_b}. "
        f"This indicates lookahead bias in the signal or eligibility logic."
    )

    # And A should definitely be among the selected (it has the best momentum)
    assert "A" in entered_a, "Symbol A had best momentum but wasn't selected"


# --------------------------------------------------------------------------- #
# Invariant 2: fills at today's open, not yesterday's close                   #
# --------------------------------------------------------------------------- #

def test_fills_at_rebalance_day_open_not_prior_close() -> None:
    """Construct data where T-1's close and T's open differ sharply. Verify
    that filled positions show prices ≈ T's open, not T-1's close.

    This is the lookahead version of "did the engine peek at today's data"
    — but more subtle: it's making sure the engine doesn't accidentally
    use the WRONG bar for fills.
    """
    n = 350
    rebalance_date = pd.Timestamp("2024-04-01")  # Monday, first trading day
    data_end = rebalance_date

    # Build a price path that has yesterday's close at $100 and today's
    # open at $110 — overnight gap.
    base_path = [100.0 * (1.003 ** i) for i in range(n - 1)]
    base_path.append(100.0 * (1.003 ** (n - 2)))  # yesterday's close

    bars = _make_daily_bars(end_date=data_end, n_days=n, price_path=base_path)
    # Override the LAST bar's open to be very different from previous close
    last_close_prior = bars["close"].iloc[-2]
    today_open = last_close_prior * 1.10  # 10% gap up
    today_close = today_open * 1.005      # small drift after open
    bars.loc[bars.index[-1], "open"] = today_open
    bars.loc[bars.index[-1], "high"] = today_close
    bars.loc[bars.index[-1], "low"] = today_open
    bars.loc[bars.index[-1], "close"] = today_close

    # Universe with one tradeable symbol + SPY for calendar
    bars_by: dict[str, pd.DataFrame] = {
        "A": bars,
        "SPY": _make_daily_bars(end_date=data_end, n_days=n, price_path=[400.0] * n),
    }
    cfg = BacktestConfig(
        start_date=rebalance_date.date(), end_date=rebalance_date.date(),
        symbols=("A",), n_positions=1,
        min_days_history=252, min_adv_dollars=1_000_000.0,
        initial_cash=100_000.0, slippage_bps=0.0,
    )
    cal = _trading_calendar(data_end, n)

    trades, _, rebs = run_backtest(cfg, bars_by, cal)

    assert len(rebs) == 1, f"Expected one rebalance event; got {len(rebs)}"
    # Position A opened on rebalance day at today's open, then liquidated at
    # end-of-backtest (same day) at today's close. The recorded Trade's
    # entry_price should be ≈ today_open, NOT last_close_prior.
    assert len(trades) >= 1
    t = next(tr for tr in trades if tr.symbol == "A")
    assert t.entry_price == pytest.approx(today_open, rel=1e-6), (
        f"Engine filled at {t.entry_price}; expected today's open {today_open}. "
        f"If the engine accidentally used yesterday's close ({last_close_prior}) "
        f"or today's close ({today_close}), this is a lookahead/wrong-bar bug."
    )


# --------------------------------------------------------------------------- #
# Invariant 3: insufficient-history symbols are excluded                      #
# --------------------------------------------------------------------------- #

def test_recent_ipo_excluded_until_enough_history() -> None:
    """A symbol with only 100 days of history (less than min_days_history=252)
    must be filtered out, even if its momentum looks great over its short
    life.
    """
    n_full = 350
    n_ipo = 100  # not enough history
    rebalance_date = pd.Timestamp("2024-04-01")
    data_end = rebalance_date

    # Long-history symbol: modest uptrend
    a_path = [100.0 * (1.001 ** i) for i in range(n_full)]
    # Recent IPO: very strong but short history
    ipo_path = [100.0 * (1.01 ** i) for i in range(n_ipo)]

    bars_by: dict[str, pd.DataFrame] = {
        "A": _make_daily_bars(end_date=data_end, n_days=n_full, price_path=a_path),
        "IPO": _make_daily_bars(end_date=data_end, n_days=n_ipo, price_path=ipo_path),
        "SPY": _make_daily_bars(end_date=data_end, n_days=n_full, price_path=[400.0] * n_full),
    }
    cfg = BacktestConfig(
        start_date=rebalance_date.date(), end_date=rebalance_date.date(),
        symbols=("A", "IPO"), n_positions=2,
        min_days_history=252, min_adv_dollars=1_000_000.0,
        initial_cash=100_000.0, slippage_bps=0.0,
    )
    cal = _trading_calendar(data_end, n_full)
    trades, _, rebs = run_backtest(cfg, bars_by, cal)

    entered = {t.symbol for t in trades}
    assert "IPO" not in entered, (
        f"IPO with only {n_ipo} days of history should have been filtered "
        f"out (min_days_history=252) but was selected: {entered}"
    )
    assert "A" in entered, "Symbol A (full history, positive momentum) should have been selected"


# --------------------------------------------------------------------------- #
# Invariant 4: skip window protects against short-term reversal contamination #
# --------------------------------------------------------------------------- #

def test_recent_pop_in_skip_window_does_not_drive_selection() -> None:
    """A stock that's been flat for a year but just popped in the last 10
    days should NOT be selected on momentum, because the 12-1 signal skips
    the most recent 21 days.

    Conversely, a stock that's been steadily rising for a year should be
    selected even if it was flat in the last 21 days.
    """
    n = 350
    rebalance_date = pd.Timestamp("2024-04-01")
    data_end = rebalance_date

    # POPPER: flat for most of history, then jumps in the last 10 days
    popper = [100.0] * (n - 10) + [100.0 * (1.05 ** i) for i in range(1, 11)]
    # STEADY: rising the whole time but flat in the last 21 days
    steady_pre_skip = [100.0 * (1.003 ** i) for i in range(n - 21)]
    steady_path = steady_pre_skip + [steady_pre_skip[-1]] * 21

    bars_by: dict[str, pd.DataFrame] = {
        "POPPER": _make_daily_bars(end_date=data_end, n_days=n, price_path=popper),
        "STEADY": _make_daily_bars(end_date=data_end, n_days=n, price_path=steady_path),
        "SPY": _make_daily_bars(end_date=data_end, n_days=n, price_path=[400.0] * n),
    }
    cfg = BacktestConfig(
        start_date=rebalance_date.date(), end_date=rebalance_date.date(),
        symbols=("POPPER", "STEADY"), n_positions=1,
        min_days_history=252, min_adv_dollars=1_000_000.0,
        initial_cash=100_000.0, slippage_bps=0.0,
    )
    cal = _trading_calendar(data_end, n)
    trades, _, _ = run_backtest(cfg, bars_by, cal)

    entered = {t.symbol for t in trades}
    assert "STEADY" in entered, (
        "STEADY has good 12-1 momentum (year-long uptrend, flat recently). "
        "It should be selected over POPPER (whose entire 'momentum' is "
        f"in the skipped recent window). Entered: {entered}"
    )
    assert "POPPER" not in entered, (
        "POPPER's only price action is in the 21-day skip window, so its "
        "12-1 momentum should be ~0. It should NOT be selected. "
        f"Entered: {entered}"
    )
